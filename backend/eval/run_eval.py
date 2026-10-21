"""Оценка точности агента: python -m eval.run_eval [--only id1,id2]

Метрики:
  fact_accuracy        — доля эталонных фактов, найденных в ответе (вердикт + разделы + top-k) с допуском 0.5%;
  verdict_fact_accuracy— доля эталонных числовых фактов, присутствующих именно в тексте вердикта;
  type_accuracy        — точность классификации типа вопроса;
  verdict_hallucination— доля ответов, где fact-check нашёл в вердикте числа, отсутствующие в расчётах.
Отчёт: docs/EVAL.md + eval/results.json
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from app.agent.agent import ask
from app.db import query
from eval.cases import CASES

ROOT = Path(__file__).resolve().parents[2]
NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _flatten(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


def _nums(text: str) -> list[float]:
    text = re.sub(r"(?<=\d)[\s ](?=\d{3}\b)", "", text)  # «1 234 567» → 1234567
    return [float(n.replace(",", ".")) for n in NUM.findall(text)]


def _scaled(text: str) -> list[float]:
    out = []
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(млрд|млн|тыс)", text):
        out.append(float(m.group(1).replace(",", ".")) * {"тыс": 1e3, "млн": 1e6, "млрд": 1e9}[m.group(2)])
    return out


def match_number(gt: float, text: str) -> bool:
    for v in _nums(text) + _scaled(text):
        tol = max(0.005 * abs(gt), 0.51 if abs(gt) >= 10 else 0.0051)
        if abs(v - gt) <= tol:
            return True
    return False


def _from_log(q: str) -> dict | None:
    r = query("SELECT answer FROM ops.qa_log WHERE question = %(q)s ORDER BY created_at DESC LIMIT 1", {"q": q})
    return r[0]["answer"] if r else None


def run(only: set[str] | None = None, resume: bool = False, pause: float = 0, from_log: bool = False) -> dict:
    prev = {}
    rp = ROOT / "backend" / "eval" / "results.json"
    if rp.exists():  # прошлые успешные ответы: при --resume не перезапрашиваются, при --only сохраняются для остальных
        prev = {r["id"]: r for r in json.loads(rp.read_text())["results"] if not r.get("error")}
    results = []
    for case in CASES:
        if only and case["id"] not in only:
            if case["id"] in prev:          # не теряем ранее полученные ответы при частичном прогоне
                results.append(prev[case["id"]])
            continue
        if resume and case["id"] in prev:
            results.append(prev[case["id"]])
            continue
        if pause and results:
            time.sleep(pause)
        subst = {}
        if "lot_sql" in case:
            subst["lot2"] = list(query(case["lot_sql"])[0].values())[0]
        q = case["q"].format(**subst)
        facts = []
        for f in case["facts"]:
            v = list(query(f["sql"].format(**subst))[0].values())[0]
            facts.append({**f, "gt": float(v) if f["kind"] == "number" and v is not None else v})
        t0 = time.time()
        logged = _from_log(q) if from_log else None
        if from_log and logged is None:
            continue                       # в режиме --from-log неотвеченные вопросы пропускаются
        try:
            a = logged or ask(q)
            err = None
        except Exception as e:  # noqa: BLE001
            a, err = {}, repr(e)
        verdict = a.get("sections", {}).get("verdict", "")
        whole = verdict + "\n" + _flatten(a.get("sections", {})) + "\n" + _flatten(a.get("evidence", []))
        checked = []
        for f in facts:
            if f["gt"] is None:
                continue
            if f["kind"] == "number":
                ok_any, ok_verdict = match_number(f["gt"], whole), match_number(f["gt"], verdict)
            else:
                ok_any, ok_verdict = str(f["gt"]) in whole, str(f["gt"]) in verdict
            checked.append({"name": f["name"], "gt": f["gt"], "found": ok_any, "in_verdict": ok_verdict})
        results.append({
            "id": case["id"], "question": q, "expected_type": case["type"], "got_type": a.get("question_type"),
            "facts": checked, "verdict": verdict, "unverified_numbers": a.get("unverified_numbers", []), "latency_s": a.get("latency_s", round(time.time() - t0, 1)),
            "tools": [t["tool"] for t in a.get("trace", [])], "error": err, "answer_id": a.get("id"),
            "model": a.get("llm", {}).get("model"),
        })
        print(f"[{case['id']}] type={a.get('question_type')} facts={sum(c['found'] for c in checked)}/{len(checked)} "
              f"verdict={sum(c['in_verdict'] for c in checked)}/{len(checked)} {(err or '')[:120]}")

    ok = [r for r in results if not r["error"]]           # ошибки API (квоты/сеть) — не ответы агента
    nf = sum(len(r["facts"]) for r in ok)
    summary = {
        "n_questions": len(results),
        "n_answered": len(ok),
        "n_api_errors": len(results) - len(ok),
        "n_facts": nf,
        "fact_accuracy": sum(c["found"] for r in ok for c in r["facts"]) / nf if nf else None,
        "verdict_fact_accuracy": sum(c["in_verdict"] for r in ok for c in r["facts"]) / nf if nf else None,
        "type_accuracy": sum(r["expected_type"] == r["got_type"] or
                             {r["expected_type"], r["got_type"]} <= {"analytics", "comparison", "search"}
                             for r in ok) / len(ok) if ok else None,
        "type_accuracy_strict": sum(r["expected_type"] == r["got_type"] for r in ok) / len(ok) if ok else None,
        "verdict_hallucination_rate": sum(bool(r["unverified_numbers"]) for r in ok) / len(ok) if ok else None,
        "avg_latency_s": sum(r["latency_s"] for r in ok) / len(ok) if ok else None,
        "models": sorted({r.get("model") for r in ok if r.get("model")}),
    }
    out = {"summary": summary, "results": results}
    (ROOT / "backend" / "eval" / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    _report(out)
    return out


def _pct(v) -> str:
    return "—" if v is None else f"{v:.1%}"


def _report(out: dict) -> None:
    s = out["summary"]
    lines = ["# Оценка точности AI-агента", "",
             f"Вопросов: **{s['n_questions']}**, получено ответов: **{s['n_answered']}**"
             + (f" (ещё {s['n_api_errors']} не выполнены из-за ошибок/квот API LLM, в метриках не учитываются)" if s["n_api_errors"] else "")
             + f". Эталонных фактов в отвеченных вопросах: **{s['n_facts']}**. Эталон считается независимыми SQL-запросами "
             "(`backend/eval/cases.py`). Модель: " + ", ".join(f"`{m}`" for m in s.get("models", [])) + ".", "",
             "| Метрика | Значение |", "|---|---|",
             f"| Точность извлечения фактов (ответ целиком) | **{_pct(s['fact_accuracy'])}** |",
             f"| Факты, названные в тексте вердикта | {_pct(s['verdict_fact_accuracy'])} |",
             f"| Классификация типа вопроса (search/comparison/analytics считаются одной группой) | {_pct(s['type_accuracy'])} |",
             f"| Классификация типа вопроса (строго) | {_pct(s['type_accuracy_strict'])} |",
             f"| Ответы с неподтверждёнными числами в вердикте | {_pct(s['verdict_hallucination_rate'])} |",
             f"| Средняя задержка ответа | {s['avg_latency_s']:.1f} с |" if s["avg_latency_s"] else "| Средняя задержка | — |", "",
             "## По вопросам", "", "| id | Вопрос | Модель | Тип (ожид./факт.) | Факты | Инструменты |", "|---|---|---|---|---|---|"]
    for r in out["results"]:
        f = "ошибка API" if r["error"] else ", ".join(f"{'✅' if c['found'] else '❌'} {c['name']}" for c in r["facts"])
        lines.append(f"| {r['id']} | {r['question'][:90]} | {r.get('model') or '—'} | {r['expected_type']} / {r['got_type']} | {f} | {', '.join(r['tools'])} |")
    lines += _history()
    (ROOT / "docs" / "EVAL.md").write_text("\n".join(lines) + "\n")


def _history() -> list[str]:
    """Результат до исправлений по итогам разбора ошибок (сохранён в results_before_fixes.json)."""
    p = ROOT / "backend" / "eval" / "results_before_fixes.json"
    if not p.exists():
        return []
    b = json.loads(p.read_text())["summary"]
    return ["", "## История прогонов", "",
            f"1. **До исправлений:** точность фактов {_pct(b['fact_accuracy'])} ({round(b['fact_accuracy'] * b['n_facts'])}/{b['n_facts']}), "
            f"ответов с неподтверждёнными числами {_pct(b['verdict_hallucination_rate'])}. Разбор промахов:",
            "   * `lots_count`: агент посчитал объявления вместо лотов. В описании `run_sql` не было таблицы `core.lot` → "
            "добавлены `core.lot` с колонками и пояснение «лот ≠ объявление»;",
            "   * `anomaly_customer`: модель сама добавила фильтр «2024 год», которого не было в вопросе → в промпт добавлено "
            "правило не придумывать фильтры;",
            "   * fact-check считал выдумкой целое число с отброшенными копейками (2908352108 при 2908352108,54) → для целых "
            "чисел допуск расширен до «меньше единицы» (тест `test_fact_check_accepts_truncated_integers`).",
            "2. **После исправлений:** эти два вопроса прогнаны заново, результат в таблицах выше. Остальные ответы не менялись.",
            "", "Прогон смешанный: из-за бесплатной квоты Gemini (20 запросов в сутки на модель) вопросы распределены между "
            "несколькими моделями (колонка «Модель»). Для итогового отчёта рекомендуется перепрогон на одной модели: "
            "`python -m eval.run_eval`."]



if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--resume", action="store_true", help="не перезапрашивать уже отвеченные вопросы")
    ap.add_argument("--pause", type=float, default=0, help="пауза между вопросами, с (для бесплатных квот)")
    ap.add_argument("--from-log", action="store_true", help="оценить сохранённые ответы из ops.qa_log без вызова LLM")
    args = ap.parse_args()
    r = run(set(args.only.split(",")) if args.only else None, args.resume, args.pause, args.from_log)
    print(json.dumps(r["summary"], ensure_ascii=False, indent=1))
