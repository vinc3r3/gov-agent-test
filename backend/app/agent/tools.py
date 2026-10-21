"""Инструменты агента: JSON-схемы + исполнение. Все вычисления — SQL/Python, не LLM."""
from __future__ import annotations

import json
import re

import psycopg
from psycopg.rows import dict_row

from app.analytics.anomalies import find_price_anomalies
from app.analytics.common import _clean
from app.analytics.fair_price import assess_fair_price
from app.analytics.search import aggregate, list_customers, lookup_customer, lookup_enstru, search_procurements
from app.analytics.volumes import detect_volume_anomalies
from app.config import settings

YEAR = {"type": "integer", "description": "Финансовый год (2024, 2025 или 2026)"}
BIN_LIST = {"type": "array", "items": {"type": "string"}, "description": "БИН заказчиков (12 цифр)"}
TOPK = {"type": "integer", "description": "Сколько примеров вернуть (по умолчанию 10)"}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


TOOLS = [
    {"name": "find_price_anomalies",
     "description": "АНОМАЛИИ ЦЕН. Находит позиции договоров, цена которых отклоняется от средневзвешенной по тому же "
                    "коду ЕНС ТРУ более чем на threshold_pct (по умолчанию 30%). Можно сузить по ЕНС ТРУ, БИН, году, "
                    "названию товара. Возвращает N проанализированных, top-k с ID договоров и ссылками.",
     "parameters": _obj({"threshold_pct": {"type": "number"}, "enstru_code": {"type": "string", "description": "Код или префикс ЕНС ТРУ"},
                         "customer_bin": {"type": "string"}, "year": YEAR,
                         "text": {"type": "string", "description": "Подстрока названия товара/услуги"},
                         "direction": {"type": "string", "enum": ["up", "down", "both"]},
                         "include_services": {"type": "boolean", "description": "Включать услуги/работы (по умолчанию нет)"},
                         "top_k": TOPK})},
    {"name": "assess_fair_price",
     "description": "СПРАВЕДЛИВОСТЬ ЦЕНЫ. Оценивает адекватность цены конкретного лота/договора относительно "
                    "аналогичных контрактов ДРУГИХ заказчиков (тот же ЕНС ТРУ и ед. изм.), приоритетно в том же городе "
                    "поставки, с поправками на инфляцию (ИПЦ), регион и сезонность (метрика Fair Price). "
                    "Укажите номер лота (например '81064468-ЗЦП3'), ID лота, ID договора или номер объявления.",
     "parameters": _obj({"lot_number": {"type": "string"}, "lot_id": {"type": "integer"}, "contract_id": {"type": "integer"},
                         "announce_number": {"type": "string", "description": "Номер объявления, напр. '17751600-1'"},
                         "same_city_only": {"type": "boolean", "description": "Только аналоги в том же городе поставки"},
                         "top_k": TOPK})},
    {"name": "detect_volume_anomalies",
     "description": "ОБЪЁМЫ. Выявляет нетипичное завышение количества ТРУ в годовом плане заказчика по сравнению с "
                    "предыдущими годами (с поправкой на общий тренд по другим заказчикам того же ЕНС ТРУ).",
     "parameters": _obj({"year": YEAR, "customer_bin": {"type": "string"}, "enstru_code": {"type": "string"},
                         "text": {"type": "string"}, "ratio_threshold": {"type": "number", "description": "Во сколько раз рост (по умолчанию 2.0)"},
                         "min_amount": {"type": "number", "description": "Мин. прирост суммы, тенге (по умолчанию 1 000 000)"},
                         "top_k": TOPK})},
    {"name": "search_procurements",
     "description": "ПОИСК. Ищет позиции договоров (а если их нет — лоты) по названию товара, ЕНС ТРУ, БИН, году, "
                    "поставщику. Возвращает итоги (кол-во, сумма, медиана/средневзвешенная цена) и top-k со ссылками.",
     "parameters": _obj({"text": {"type": "string"}, "customer_bins": BIN_LIST, "enstru_code": {"type": "string"},
                         "years": {"type": "array", "items": {"type": "integer"}}, "supplier_biin": {"type": "string"},
                         "min_amount": {"type": "number"}, "order_by": {"type": "string", "enum": ["amount", "date", "price"]},
                         "top_k": TOPK})},
    {"name": "aggregate",
     "description": "СРАВНЕНИЕ / АНАЛИТИКА. Агрегирует позиции договоров по группе (customer, supplier, enstru, year, "
                    "month, city, oblast, trade_method): сумма, доля, кол-во договоров, средняя/медианная/средневзвешенная "
                    "цена за единицу. Для сравнения заказчиков передайте customer_bins и group_by='customer'.",
     "parameters": _obj({"group_by": {"type": "string", "enum": ["customer", "supplier", "enstru", "year", "month", "city", "oblast", "trade_method"]},
                         "metric": {"type": "string", "enum": ["sum", "count", "avg_price", "median_price", "wavg_price"]},
                         "text": {"type": "string"}, "customer_bins": BIN_LIST, "enstru_code": {"type": "string"},
                         "years": {"type": "array", "items": {"type": "integer"}}, "supplier_biin": {"type": "string"},
                         "top_k": TOPK}, ["group_by"])},
    {"name": "lookup_enstru",
     "description": "Справочник: подобрать коды ЕНС ТРУ по названию товара/услуги (с числом позиций в данных).",
     "parameters": _obj({"text": {"type": "string"}}, ["text"])},
    {"name": "lookup_customer",
     "description": "Справочник: найти БИН заказчика из 27 организаций по части названия.",
     "parameters": _obj({"text": {"type": "string"}}, ["text"])},
    {"name": "list_customers",
     "description": "Справочник: список 27 организаций (БИН, название, объём договоров).",
     "parameters": _obj({})},
    {"name": "run_sql",
     "description": "Read-only SQL (только SELECT) по витринам, когда другие инструменты не подходят. Таблицы: "
                    "mart.price_obs (позиции договоров: contract_id, contract_number, trd_buy_id, lot_number, customer_bin, "
                    "customer_name, supplier_biin, supplier_name, enstru_code, enstru_name, item_name, base_unit, unit_price, "
                    "unit_price_adj, qty_base, total_sum, sign_date, fin_year, city_name, oblast_name, trade_method_id, "
                    "contract_url, announce_url); core.lot (лоты: id, lot_number, trd_buy_id, customer_bin, name_ru, qty, amount, "
                    "status_id, is_deleted); core.trd_buy (объявления: id, number_anno, customer_bin, org_bin, publish_date, "
                    "total_sum, fin_year); core.contract (договоры: id, contract_number, customer_bin, supplier_biin, fin_year, "
                    "sign_date, contract_sum); core.plan_point (пункты плана); core.subject (bin, name_ru); mart.lot_price, "
                    "mart.volume_yearly, mart.enstru_stats. Лот ≠ объявление: в одном объявлении (trd_buy) может быть много лотов. "
                    "Лимит 200 строк.",
     "parameters": _obj({"sql": {"type": "string"}}, ["sql"])},
    {"name": "final_answer",
     "description": "ОБЯЗАТЕЛЬНО вызвать в конце. Тип вопроса, язык и краткий вердикт (1–3 предложения) — "
                    "только с числами из результатов инструментов.",
     "parameters": _obj({"question_type": {"type": "string", "enum": ["search", "comparison", "analytics", "anomaly", "fairness", "volume"]},
                         "language": {"type": "string", "enum": ["ru", "kk"]},
                         "verdict": {"type": "string"}}, ["question_type", "language", "verdict"])},
]

ANALYTIC = {"find_price_anomalies", "assess_fair_price", "detect_volume_anomalies", "search_procurements", "aggregate", "run_sql"}

_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|call|do|vacuum|set|reset)\b", re.I)


def run_sql(sql: str) -> dict:
    s = sql.strip().rstrip(";")
    if not re.match(r"^\s*(select|with)\b", s, re.I) or ";" in s or _FORBIDDEN.search(s):
        return {"error": "Разрешён только один SELECT-запрос."}
    # роль agent_ro: SELECT только на core/mart, read-only по умолчанию, statement_timeout 15s
    try:
        with psycopg.connect(settings.database_url_ro, row_factory=dict_row) as c:
            c.execute("SET TRANSACTION READ ONLY")
            rows = c.execute(f"SELECT * FROM ({s}) q LIMIT 200").fetchall()
    except psycopg.Error as e:
        return {"error": f"SQL: {type(e).__name__}: {str(e).splitlines()[0][:300]}"}
    return {"tool": "run_sql", "question_type": "analytics", "params": {"sql": s},
            "method": "Прямой read-only SQL-запрос к витринам", "summary": {"n_rows": len(rows)},
            "sample": {"n_objects": len(rows)}, "confidence": {"level": "средняя", "reasons": ["произвольный SQL"]},
            "limitations": ["Результат произвольного SQL не проходит стандартные проверки метода."],
            "items": _clean(rows[:50]), "columns": list(rows[0].keys())[:8] if rows else []}


DISPATCH = {
    "find_price_anomalies": find_price_anomalies,
    "assess_fair_price": assess_fair_price,
    "detect_volume_anomalies": detect_volume_anomalies,
    "search_procurements": search_procurements,
    "aggregate": aggregate,
    "lookup_enstru": lookup_enstru,
    "lookup_customer": lookup_customer,
    "list_customers": list_customers,
    "run_sql": run_sql,
}


def execute(name: str, args: dict) -> tuple[dict, bool]:
    fn = DISPATCH.get(name)
    if not fn:
        return {"error": f"Неизвестный инструмент {name}"}, True
    try:
        out = fn(**{k: v for k, v in args.items() if v is not None and not k.startswith("_")})
        out = _clean(out)
        return out, "error" in out
    except TypeError as e:
        return {"error": f"Неверные параметры: {e}"}, True
    except Exception as e:  # noqa: BLE001 — ошибка уходит модели как tool_result is_error
        return {"error": f"{type(e).__name__}: {str(e)[:300]}"}, True


def for_llm(result: dict, max_items: int = 10) -> str:
    """Компактное представление результата для модели (top-k обрезается)."""
    r = dict(result)
    if isinstance(r.get("items"), list):
        r["items"] = r["items"][:max_items]
    return json.dumps(r, ensure_ascii=False, default=str)[:12000]
