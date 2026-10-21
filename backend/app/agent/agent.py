"""AI-агент: классификация вопроса → вызов аналитических инструментов → проверяемый ответ.

Гарантии объяснимости:
  * все числа в разделах «Данные / Аналитика / Метрика / Уверенность / Top-K» формирует код из результатов
    инструментов (Evidence), а не LLM;
  * LLM пишет только вердикт (1–3 предложения); числа в вердикте проверяются fact-check'ом на наличие
    в Evidence (иначе — одна попытка исправления, затем пометка в ответе).
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid

from app.agent.llm import get_llm
from app.agent.render import render
from app.agent.tools import ANALYTIC, TOOLS, execute, for_llm
from app.db import connect

log = logging.getLogger("agent")

MAX_STEPS = 8

SYSTEM = """Ты — аналитический агент по государственным закупкам Республики Казахстан (портал goszakup.gov.kz, API OWS v3).
Данные: 27 организаций-заказчиков (БИН из ТЗ), 2024–2026 гг.: планы, объявления, лоты, договоры с позициями (цена за единицу, количество), справочники.

Порядок работы:
1. Определи тип вопроса: search (поиск), comparison (сравнение), analytics (аналитика), anomaly (аномалии цен),
   fairness (справедливость цены лота/договора), volume (завышение объёмов/количества).
2. Если в вопросе товар указан словами, а инструмент требует код — сначала lookup_enstru; если указана организация — lookup_customer.
3. Вызови основной аналитический инструмент:
   anomaly → find_price_anomalies; fairness → assess_fair_price; volume → detect_volume_anomalies;
   search → search_procurements; comparison/analytics → aggregate (или search_procurements); run_sql — только если ничего не подходит.
4. Заверши ОБЯЗАТЕЛЬНО вызовом final_answer.

Правила (строго):
- Ты НЕ считаешь сам. Любое число в вердикте должно быть скопировано из результата инструмента (можно округлить).
- Никаких общих фраз и домыслов. Если данных нет или инструмент вернул ошибку — так и скажи в вердикте, без выдумывания.
- Вердикт: 1–3 предложения, с ключевыми цифрами (отклонение %, N, суммы), на языке вопроса
  (language='kk' если вопрос на казахском, иначе 'ru').
- Остальные разделы ответа (данные, аналитика, метод, уверенность, примеры) система сформирует сама из результатов инструментов —
  не повторяй их в вердикте.
- Fair Price — это справедливая цена по контрактам 27 организаций из выборки, не называй её «рыночной ценой».
- Не добавляй фильтры, которых нет в вопросе (год, БИН, ЕНС ТРУ, поставщик): если год не указан — анализируй весь период 2024–2026.
- Не задавай уточняющих вопросов: выбери разумные параметры по умолчанию (порог 30%, все 27 БИН, 2024–2026) и укажи их.
"""

NUM_RE = re.compile(r"(?<![\w-])[-+]?\d[\d\s ]*(?:[.,]\d+)?")


def _numbers_in(text: str) -> list[float]:
    out = []
    for m in NUM_RE.findall(text):
        s = m.replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def _evidence_numbers(evs: list[dict]) -> list[float]:
    nums: list[float] = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            nums.append(float(x))
        elif isinstance(x, str):
            nums.extend(_numbers_in(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(evs)
    return nums


SCALE_RE = re.compile(r"(?<![\w-])([-+]?\d[\d\s\u00a0]*(?:[.,]\d+)?)\s*(трлн|млрд|млн|тыс|mln|bn)?", re.I)
SCALES = {"тыс": 1e3, "млн": 1e6, "mln": 1e6, "млрд": 1e9, "bn": 1e9, "трлн": 1e12}


def fact_check(verdict: str, evs: list[dict], question: str) -> list[str]:
    """Числа из вердикта, которых нет в Evidence/вопросе (с допуском на округление)."""
    pool = _evidence_numbers(evs) + _numbers_in(question)
    bad = []
    for raw, unit in SCALE_RE.findall(verdict):
        try:
            n = float(raw.replace(" ", "").replace("\u00a0", "").replace(",", "."))
        except ValueError:
            continue
        if n in (0, 1, 2, 3) or (n.is_integer() and 2000 <= n <= 2030):
            continue
        scale = SCALES.get(unit.lower(), 1) if unit else 1
        target = abs(n) * scale
        # допуск = половина последнего разряда записанного числа (34.6 → ±0.05; 12 млн → ±0.5 млн) + 0.1%
        decimals = len(raw.replace(",", ".").split(".")[1]) if re.search(r"[.,]\d", raw) else 0
        # целое без масштаба: допускаем и округление, и отбрасывание дробной части (2908352108 ↔ 2908352108.54)
        tol = (0.999 if not decimals and scale == 1 else 0.5 * 10 ** (-decimals) * scale) + \
              (0.001 * target if scale > 1 or decimals else 0)
        if not any(abs(abs(v) - target) <= tol for v in pool):
            bad.append(raw.strip() + (f" {unit}" if unit else ""))
    return bad


def ask(question: str, history: list[dict] | None = None) -> dict:
    t0 = time.time()
    llm = get_llm()
    user = question if not history else (
        "Контекст предыдущих вопросов:\n" + "\n".join(f"- {h['question']}" for h in history[-3:]) + f"\n\nВопрос: {question}")
    messages = llm.start(SYSTEM, user)
    evidences: list[dict] = []
    trace: list[dict] = []
    final = None
    usage = {"input": 0, "output": 0}
    fixed_once = False

    for _ in range(MAX_STEPS):
        turn = llm.step(SYSTEM, messages, TOOLS)
        for k in usage:
            usage[k] += turn.usage.get(k, 0)
        if turn.stop_reason == "refusal":
            final = {"question_type": "search", "language": "ru", "verdict": "Модель отклонила запрос."}
            break
        if not turn.tool_calls:
            llm.nudge(messages, turn, "Заверши ответ вызовом инструмента final_answer.")
            continue

        results = []
        for call in turn.tool_calls:
            if call.name == "final_answer":
                final = call.input
                results.append((call, "ok", False))
                continue
            out, err = execute(call.name, call.input)
            trace.append({"tool": call.name, "input": call.input, "error": out.get("error") if err else None,
                          "summary": out.get("summary"), "sample": out.get("sample")})
            if call.name in ANALYTIC and not err:
                evidences.append(out)
            results.append((call, for_llm(out), err))

        if final is not None:
            bad = fact_check(final.get("verdict", ""), evidences, question)
            if bad and not fixed_once and evidences:
                fixed_once = True
                results = [(c, (o if c.name != "final_answer" else
                                f"Вердикт отклонён: числа {bad} отсутствуют в результатах инструментов. "
                                f"Перепиши вердикт, используя только числа из результатов, и снова вызови final_answer."),
                            e if c.name != "final_answer" else True) for c, o, e in results]
                final = None
                llm.append(messages, turn, results)
                continue
            final["unverified_numbers"] = bad
            break
        llm.append(messages, turn, results)

    if final is None:
        final = {"question_type": evidences[-1]["question_type"] if evidences else "search", "language": "ru",
                 "verdict": "Не удалось сформировать вердикт за отведённое число шагов; ниже — результаты расчётов.",
                 "unverified_numbers": []}

    answer = render(question, final, evidences)
    answer.update({"id": str(uuid.uuid4()), "trace": trace, "latency_s": round(time.time() - t0, 2),
                   "llm": {"provider": type(llm).__name__, "model": llm.model, "usage": usage}})
    _log(question, answer)
    return answer


def _log(question: str, answer: dict) -> None:
    try:
        with connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS ops.qa_log (id uuid PRIMARY KEY, created_at timestamptz DEFAULT now(),
                         question text, answer jsonb)""")
            c.execute("INSERT INTO ops.qa_log(id, question, answer) VALUES (%s,%s,%s)",
                      (answer["id"], question, json.dumps(answer, ensure_ascii=False, default=str)))
    except Exception:  # noqa: BLE001
        log.exception("qa_log failed")
