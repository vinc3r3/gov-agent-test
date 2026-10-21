"""HTTP API агента.

POST /api/ask            — вопрос → структурированный ответ
GET  /api/health         — состояние, свежесть данных
GET  /api/dq             — последний отчёт качества данных
GET  /api/examples       — примеры вопросов
GET  /api/history        — последние вопросы/ответы
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.agent.agent import ask
from app.agent.render import current_method
from app.config import settings
from app.db import query

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(title="Goszakup AI Agent", version="1.0")
# UI ходит в API через прокси (vite / nginx) с того же origin; CORS нужен только для явно разрешённых источников
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

EXAMPLES = [
    {"type": "anomaly", "q": "Найди закупки с отклонением цены > 30% от средневзвешенной по данному ЕНСТРУ"},
    {"type": "anomaly", "q": "Найди аномально дорогие закупки дизельного топлива в 2025 году"},
    {"type": "fairness", "q": "Оцени адекватность цены лота №82166119-ЗЦП2 относительно аналогичных контрактов других ведомств в идентичном городе поставки"},
    {"type": "volume", "q": "Выяви нетипичное завышение количества ТРУ в 2026 году по сравнению с предыдущими годами"},
    {"type": "comparison", "q": "Сравни объёмы закупок по заказчикам за 2025 год"},
    {"type": "search", "q": "Найди договоры на поставку бумаги А4 в 2026 году"},
    {"type": "analytics", "q": "Какие поставщики получили больше всего договоров в 2025 году?"},
    {"type": "anomaly", "q": "2025 жылы ЕНСТРУ бойынша орташа өлшемді бағадан 30%-дан астам ауытқыған сатып алуларды тап"},
]


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    history: list[dict] | None = None


@app.post("/api/ask")
async def api_ask(body: AskIn):
    try:
        return await run_in_threadpool(ask, body.question, body.history)
    except Exception as e:  # noqa: BLE001
        logging.exception("ask failed")
        status = getattr(e, "status_code", None)  # ошибки SDK провайдеров LLM несут HTTP-статус
        if status == 429:
            raise HTTPException(429, "Исчерпана квота LLM-провайдера (лимит запросов). Попробуйте позже или "
                                     "переключите модель в .env (OPENAI_COMPAT_MODEL / ANTHROPIC_MODEL).")
        if status in (500, 502, 503, 504, 529):
            raise HTTPException(503, "LLM-провайдер сейчас перегружен или недоступен. Повторите вопрос через минуту.")
        if status in (401, 403):
            raise HTTPException(502, "LLM-провайдер отклонил ключ API. Проверьте ключ в .env.")
        raise HTTPException(500, f"Внутренняя ошибка: {type(e).__name__}: {str(e)[:200]}")


@app.get("/api/health")
def health():
    sync = query("SELECT mode, finished_at, error FROM ops.sync_log ORDER BY id DESC LIMIT 1")
    ok = query("SELECT max(finished_at) AS t FROM ops.sync_log WHERE error IS NULL")[0]["t"]
    counts = query("""SELECT (SELECT count(*) FROM mart.price_obs) AS price_obs, (SELECT count(*) FROM core.contract) AS contracts,
                             (SELECT count(*) FROM core.lot) AS lots, (SELECT count(*) FROM core.plan_point) AS plan_points""")[0]
    return {"status": "ok", "llm": {"provider": settings.llm_provider,
                                    "model": settings.anthropic_model if settings.llm_provider == "anthropic" else settings.openai_compat_model},
            "last_sync": sync[0] if sync else None, "last_successful_sync": ok, "counts": counts}


@app.get("/api/dq")
def dq():
    r = query("SELECT created_at, report FROM ops.dq_report ORDER BY id DESC LIMIT 1")
    return r[0] if r else {}


@app.get("/api/examples")
def examples():
    return EXAMPLES


@app.get("/api/history")
def history(limit: int = 20):
    try:
        # последний ответ на каждый уникальный вопрос (повторные прогоны не дублируют историю)
        return query("""SELECT * FROM (
                          SELECT DISTINCT ON (question) id, created_at, question,
                                 answer->>'question_type' AS question_type, answer->'sections'->>'verdict' AS verdict
                          FROM ops.qa_log ORDER BY question, created_at DESC) x
                        ORDER BY created_at DESC LIMIT %(l)s""", {"l": limit})
    except Exception:  # noqa: BLE001 — таблица создаётся при первом вопросе
        return []


@app.get("/api/answer/{answer_id}")
def get_answer(answer_id: str):
    r = query("SELECT answer FROM ops.qa_log WHERE id = %(i)s", {"i": answer_id})
    if not r:
        raise HTTPException(404)
    a = r[0]["answer"]
    # числа и вердикт — как были сохранены; описание метода — в актуальной формулировке
    main = next((e for e in reversed(a.get("evidence") or []) if e.get("tool") not in ("search_procurements", "run_sql")), None)
    if main and a.get("sections", {}).get("metric"):
        a["sections"]["metric"] = current_method(main.get("tool"), a["sections"]["metric"])
    return a
