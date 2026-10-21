"""Поиск, сравнение и агрегаты (типы вопросов: search / comparison / analytics) + справочные lookup-ы."""
from __future__ import annotations

import re

from app.analytics.common import Evidence, confidence_level
from app.config import BINS
from app.db import query

GROUP_BY = {
    "customer": ("customer_bin", "max(customer_name)"),
    "supplier": ("supplier_biin", "max(supplier_name)"),
    "enstru": ("enstru_code", "max(enstru_name)"),
    "year": ("fin_year::text", "NULL"),
    "month": ("to_char(month, 'YYYY-MM')", "NULL"),
    "city": ("city_code", "max(city_name)"),
    "oblast": ("oblast_code", "max(oblast_name)"),
    "trade_method": ("trade_method_id::text",
                     "(SELECT name_ru FROM core.ref_status r WHERE r.ref='ref_trade_methods' AND r.code = max(trade_method_id)::text)"),
}


GROUP_NAMES = {"customer": "заказчик", "supplier": "поставщик", "enstru": "ЕНС ТРУ", "year": "год", "month": "месяц",
               "city": "город", "oblast": "область", "trade_method": "способ закупки"}


def _filters(f: dict) -> tuple[str, dict]:
    cond, p = ["true"], {}
    if f.get("customer_bins"):
        cond.append("customer_bin = ANY(%(cb)s)"); p["cb"] = list(f["customer_bins"])
    if f.get("supplier_biin"):
        cond.append("supplier_biin = %(sb)s"); p["sb"] = f["supplier_biin"]
    if f.get("enstru_code"):
        cond.append("enstru_code LIKE %(e)s"); p["e"] = f["enstru_code"] + "%"
    if f.get("years"):
        cond.append("fin_year = ANY(%(y)s)"); p["y"] = [int(y) for y in f["years"]]
    if f.get("date_from"):
        cond.append("sign_date >= %(df)s"); p["df"] = f["date_from"]
    if f.get("date_to"):
        cond.append("sign_date < %(dt)s"); p["dt"] = f["date_to"]
    if f.get("text"):
        words = [w for w in re.split(r"\s+", f["text"].lower()) if len(w) > 2]
        for i, w in enumerate(words[:5]):
            cond.append(f"(item_name_clean ILIKE %(w{i})s OR enstru_name ILIKE %(w{i})s)"); p[f"w{i}"] = f"%{w[:max(4, len(w) - 2)]}%"
    if f.get("city_code"):
        cond.append("city_code = %(cc)s"); p["cc"] = f["city_code"]
    if f.get("min_amount"):
        cond.append("total_sum >= %(ma)s"); p["ma"] = f["min_amount"]
    return " AND ".join(cond), p


def search_procurements(text: str | None = None, customer_bins: list[str] | None = None, enstru_code: str | None = None,
                        years: list[int] | None = None, supplier_biin: str | None = None, min_amount: float | None = None,
                        order_by: str = "amount", top_k: int = 10) -> dict:
    f = dict(text=text, customer_bins=customer_bins, enstru_code=enstru_code, years=years,
             supplier_biin=supplier_biin, min_amount=min_amount)
    where, p = _filters(f)
    order = {"amount": "total_sum DESC", "date": "sign_date DESC", "price": "unit_price DESC"}.get(order_by, "total_sum DESC")
    agg = query(f"""SELECT count(*) n, count(DISTINCT contract_id) n_contracts, count(DISTINCT customer_bin) n_customers,
                           sum(total_sum) total, min(sign_date) d_from, max(sign_date) d_to
                    FROM mart.price_obs WHERE {where}""", p)[0]
    # цена за единицу — только по основной единице измерения выборки (смешивать «л» и «т» нельзя)
    unit = query(f"""SELECT base_unit, count(*) n,
                            percentile_cont(0.5) WITHIN GROUP (ORDER BY unit_price) med_price,
                            sum(unit_price*qty_base)/nullif(sum(qty_base),0) wavg_price
                     FROM mart.price_obs WHERE {where} AND base_unit IS NOT NULL
                     GROUP BY 1 ORDER BY 2 DESC LIMIT 1""", p)
    unit = unit[0] if unit else {"base_unit": None, "n": 0, "med_price": None, "wavg_price": None}
    rows = query(f"""SELECT contract_id, contract_number, trd_buy_id, lot_number, customer_bin, customer_name,
                            supplier_biin, supplier_name, item_name, enstru_code, enstru_name, base_unit AS unit,
                            unit_price, qty_base AS qty, total_sum, sign_date, city_name, contract_url, announce_url
                     FROM mart.price_obs WHERE {where} ORDER BY {order} LIMIT %(k)s""", {**p, "k": top_k})
    lots = []
    if text and agg["n"] == 0:  # нет договоров — ищем среди лотов (объявления без договора)
        lots = query("""SELECT lot_id, lot_number, trd_buy_id, customer_bin, customer_name, lot_name AS item_name,
                               enstru_code, enstru_name, qty, amount AS total_sum, lot_unit_price AS unit_price,
                               publish_date AS sign_date, announce_url
                        FROM mart.lot_price WHERE name_clean ILIKE %(t)s OR enstru_name ILIKE %(t)s
                        ORDER BY amount DESC NULLS LAST LIMIT %(k)s""", {"t": f"%{text.lower()}%", "k": top_k})
    ev = Evidence(
        tool="search_procurements", question_type="search",
        params={**{k: v for k, v in f.items() if v}, "period": years or "2024–2026",
                "bins": customer_bins or "все 27 БИН", "source": "позиции договоров (факт)" if not lots else "лоты"},
        method="Фильтрация позиций договоров (mart.price_obs) по названию (очищенному), ЕНС ТРУ, БИН, году; "
               "агрегаты считаются SQL по всей отфильтрованной выборке.",
        summary={"n_positions": agg["n"], "n_contracts": agg["n_contracts"], "n_customers": agg["n_customers"],
                 "total_sum": agg["total"], "price_unit": unit["base_unit"], "n_positions_in_price_unit": unit["n"],
                 "median_unit_price": unit["med_price"], "weighted_avg_unit_price": unit["wavg_price"],
                 "date_from": agg["d_from"], "date_to": agg["d_to"], "n_lots_found": len(lots)},
        sample={"n_objects": agg["n"] or len(lots)},
        confidence=confidence_level(agg["n"] or len(lots), None,
                                    ["поиск по подстроке названия может пропустить синонимы"] if text else []),
        limitations=(["Медиана/средневзвешенная цена посчитаны только по основной ед. изм. "
                      f"«{unit['base_unit']}» ({unit['n']} из {agg['n']} позиций)."] if unit["n"] != agg["n"] else []) +
                    ["Охват: 27 БИН из ТЗ, договоры 2024–2026 гг."],
        items=rows or lots,
        columns=["contract_number", "customer_name", "item_name", "unit", "unit_price", "qty", "total_sum", "sign_date"],
    )
    return ev.to_dict()


def aggregate(group_by: str, metric: str = "sum", text: str | None = None, customer_bins: list[str] | None = None,
              enstru_code: str | None = None, years: list[int] | None = None, supplier_biin: str | None = None,
              top_k: int = 15) -> dict:
    """Сравнение/аналитика: агрегаты по группам. metric: sum | count | avg_price | median_price | wavg_price."""
    if group_by not in GROUP_BY:
        return {"error": f"group_by должен быть одним из {list(GROUP_BY)}"}
    key, label = GROUP_BY[group_by]
    f = dict(text=text, customer_bins=customer_bins, enstru_code=enstru_code, years=years, supplier_biin=supplier_biin)
    where, p = _filters(f)
    price_metric = metric in ("avg_price", "median_price", "wavg_price")
    if price_metric:
        units = query(f"SELECT base_unit, count(*) n FROM mart.price_obs WHERE {where} AND base_unit IS NOT NULL "
                      "GROUP BY 1 ORDER BY 2 DESC", p)
        if not units:
            return {"error": "Нет данных по фильтру."}
        where += " AND base_unit = %(bu)s"; p["bu"] = units[0]["base_unit"]
    rows = query(f"""
        SELECT {key} AS key, {label} AS label, count(*) AS n_positions, count(DISTINCT contract_id) AS n_contracts,
               sum(total_sum) AS total_sum,
               avg(unit_price) AS avg_price,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY unit_price) AS median_price,
               sum(unit_price*qty_base)/nullif(sum(qty_base),0) AS wavg_price,
               sum(qty_base) AS qty
        FROM mart.price_obs WHERE {where}
        GROUP BY {key} ORDER BY {({'sum': 'total_sum', 'count': 'n_positions'}.get(metric, metric))} DESC NULLS LAST
        LIMIT %(k)s""", {**p, "k": top_k})
    tot = query(f"SELECT count(*) n, sum(total_sum) s FROM mart.price_obs WHERE {where}", p)[0]
    for r in rows:
        r["share_pct"] = float(r["total_sum"]) / float(tot["s"]) * 100 if tot["s"] else None
    ev = Evidence(
        tool="aggregate", question_type="comparison" if (customer_bins and len(customer_bins) > 1) or group_by in ("customer", "year") else "analytics",
        params={**{k: v for k, v in f.items() if v}, "group_by": group_by, "metric": metric,
                "unit": p.get("bu"), "period": years or "2024–2026", "bins": customer_bins or "все 27 БИН"},
        method=f"SQL-агрегация позиций договоров по группе «{GROUP_NAMES[group_by]}»: сумма, количество, средняя, медиана и "
               "средневзвешенная (Σ цена·кол-во / Σ кол-во) цена за единицу (без НДС).",
        summary={"n_groups_shown": len(rows), "total_sum_all": tot["s"], "n_positions_all": tot["n"]},
        sample={"n_objects": tot["n"]},
        confidence=confidence_level(tot["n"], None),
        limitations=["Суммы — по позициям договоров без НДС (item_price × qty), валидные статусы договоров."]
                    + (["Цены сравниваются только в одной ед. изм.: " + str(p.get("bu"))] if price_metric else []),
        items=rows,
        columns=["label", "key", "n_contracts", "total_sum", "share_pct", "wavg_price", "median_price"],
    )
    return ev.to_dict()


def lookup_enstru(text: str, limit: int = 10) -> dict:
    """Подбор кодов ЕНС ТРУ по названию товара (с числом позиций договоров в хранилище)."""
    words = [w for w in re.split(r"\s+", text.lower()) if len(w) > 2][:4] or [text.lower()]
    cond = " AND ".join(f"(e.name_ru ILIKE %(w{i})s OR e.name_kz ILIKE %(w{i})s)" for i in range(len(words)))
    p = {f"w{i}": f"%{w[:max(4, len(w) - 2)]}%" for i, w in enumerate(words)}
    rows = query(f"""SELECT e.code, e.name_ru, coalesce(s.n, 0) AS n_positions, s.base_unit
                     FROM core.ref_enstru e LEFT JOIN LATERAL (
                        SELECT sum(n) n, max(base_unit) base_unit FROM mart.enstru_stats st WHERE st.enstru_code = e.code) s ON true
                     WHERE {cond} ORDER BY coalesce(s.n, 0) DESC LIMIT %(l)s""", {**p, "l": limit})
    return {"matches": rows}


def lookup_customer(text: str) -> dict:
    """Поиск БИН заказчика (из 27 организаций ТЗ) по названию."""
    rows = query("""SELECT bin, name_ru FROM core.subject WHERE bin = ANY(%(b)s)
                    AND (name_ru ILIKE %(t)s OR name_kz ILIKE %(t)s OR bin = %(raw)s)""",
                 {"b": BINS, "t": f"%{text}%", "raw": text})
    return {"matches": rows}


def list_customers() -> dict:
    return {"customers": query("""SELECT s.bin, s.name_ru, count(p.*) AS n_positions, sum(p.total_sum) AS total_sum
                                  FROM core.subject s LEFT JOIN mart.price_obs p ON p.customer_bin = s.bin
                                  WHERE s.bin = ANY(%(b)s) GROUP BY 1, 2 ORDER BY 4 DESC NULLS LAST""", {"b": BINS})}
