"""Аномалии цен: отклонение цены позиции от средневзвешенной по ЕНС ТРУ.

Для позиции i в группе «код ЕНС ТРУ + базовая ед. изм.»:
  средневзвешенная = сумма(цена × кол-во) / сумма(кол-во) по ОСТАЛЬНЫМ позициям группы (leave-one-out),
                     иначе крупная закупка «тянет» среднюю на себя;
  цены приведены по ИПЦ к базовому месяцу, чтобы инфляция 2024→2026 не давала ложных аномалий;
  отклонение = цена / средневзвешенная − 1; аномалия, если отклонение > порога (по умолчанию 30%).
Дополнительно: robust z-score = (цена − медиана) / (1.4826 × MAD) и выход за Q3 + 1.5 × IQR.
Превышение = (цена − средневзвешенная) × кол-во — для ранжирования по денежному эффекту.
"""
from __future__ import annotations

from collections import defaultdict

from app.analytics.common import Evidence, confidence_level, mad, median, quantiles
from app.db import query

MIN_GROUP = 5

METHOD = ("Отклонение = цена позиции / средневзвешенная цена остальных позиций того же ЕНС ТРУ и ед. изм. − 1. "
          "Средневзвешенная считается по количеству и без самой позиции (leave-one-out); все цены приведены по ИПЦ "
          "к базовому месяцу. Аномалия — отклонение больше порога. Дополнительные индикаторы: robust z-score (MAD) "
          "и выход цены за Q3 + 1.5 × IQR.")


LUMPSUM_UNITS = ("услуга", "работа")


def _scope_filter(enstru_code=None, customer_bin=None, year=None, text=None, include_services=False):
    cond, p = ["enstru_code IS NOT NULL", "base_unit IS NOT NULL", "unit_price_adj IS NOT NULL"], {}
    if not include_services:  # услуги/работы «1 шт» — это сумма договора, а не цена единицы
        cond.append("base_unit <> ALL(%(lump)s)"); p["lump"] = list(LUMPSUM_UNITS)
    if enstru_code:
        cond.append("enstru_code LIKE %(e)s"); p["e"] = enstru_code + "%"
    if customer_bin:
        cond.append("customer_bin = %(b)s"); p["b"] = customer_bin
    if year:
        cond.append("fin_year = %(y)s"); p["y"] = int(year)
    if text:
        cond.append("(item_name_clean ILIKE %(t)s OR enstru_name ILIKE %(t)s)"); p["t"] = f"%{text.lower()}%"
    return " AND ".join(cond), p


def find_price_anomalies(threshold_pct: float = 30, enstru_code: str | None = None, customer_bin: str | None = None,
                         year: int | None = None, text: str | None = None, direction: str = "up",
                         include_services: bool = False, top_k: int = 10) -> dict:
    where, p = _scope_filter(enstru_code, customer_bin, year, text, include_services)
    targets = query(f"SELECT DISTINCT enstru_code, base_unit FROM mart.price_obs WHERE {where}", p)
    if not targets:
        return {"error": "По заданным фильтрам нет позиций договоров с ЕНС ТРУ."}
    keys = [(t["enstru_code"], t["base_unit"]) for t in targets]

    # группы сравнения берутся по всем 27 БИН (не только по фильтру), чтобы средняя была репрезентативной
    rows = query("""
        SELECT unit_id, contract_id, contract_number, trd_buy_id, lot_number, customer_bin, customer_name,
               supplier_biin, supplier_name, enstru_code, enstru_name, base_unit, item_name, item_name_clean,
               unit_price, unit_price_adj, qty_base, sign_date, fin_year, city_name, contract_url, announce_url
        FROM mart.price_obs
        WHERE (enstru_code, base_unit) IN (SELECT * FROM unnest(%(e)s::text[], %(u)s::text[]))
    """, {"e": [k[0] for k in keys], "u": [k[1] for k in keys]})
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["enstru_code"], r["base_unit"])].append(r)

    in_scope_ids = {r["unit_id"] for r in query(f"SELECT unit_id FROM mart.price_obs WHERE {where}", p)}
    flagged, analysed, groups_used, small_groups = [], 0, 0, 0
    for key, g in groups.items():
        if len(g) < MIN_GROUP:
            small_groups += 1
            continue
        groups_used += 1
        prices = [float(r["unit_price_adj"]) for r in g]
        S = sum(float(r["unit_price_adj"]) * float(r["qty_base"]) for r in g)
        Q = sum(float(r["qty_base"]) for r in g)
        med = median(prices)
        q1, _, q3 = quantiles(prices)
        m = mad(prices) or 0
        for r in g:
            if r["unit_id"] not in in_scope_ids:
                continue
            analysed += 1
            pa, qa = float(r["unit_price_adj"]), float(r["qty_base"])
            if Q - qa <= 0:
                continue
            wavg = (S - pa * qa) / (Q - qa)
            dev = (pa / wavg - 1) * 100 if wavg else None
            if dev is None:
                continue
            hit = dev > threshold_pct if direction == "up" else (dev < -threshold_pct if direction == "down"
                                                                    else abs(dev) > threshold_pct)
            if not hit:
                continue
            flagged.append({
                "contract_id": r["contract_id"], "contract_number": r["contract_number"], "trd_buy_id": r["trd_buy_id"],
                "lot_number": r["lot_number"], "customer_bin": r["customer_bin"], "customer_name": r["customer_name"],
                "supplier_biin": r["supplier_biin"], "supplier_name": r["supplier_name"],
                "enstru_code": r["enstru_code"], "enstru_name": r["enstru_name"], "unit": r["base_unit"],
                "item_name": r["item_name"], "sign_date": r["sign_date"], "city": r["city_name"],
                "unit_price": float(r["unit_price"]), "unit_price_adj": pa, "weighted_avg_adj": wavg,
                "median_adj": med, "deviation_pct": dev,
                "robust_z": (pa - med) / (1.4826 * m) if m else None,
                "iqr_outlier": pa > q3 + 1.5 * (q3 - q1) or pa < q1 - 1.5 * (q3 - q1),
                "qty": qa, "overpayment": (pa - wavg) * qa, "group_n": len(g),
                "contract_url": r["contract_url"], "announce_url": r["announce_url"],
            })
    flagged.sort(key=lambda x: x["overpayment"], reverse=True)
    total_over = sum(f["overpayment"] for f in flagged if f["overpayment"] > 0)
    n_groups_ok = groups_used
    lim = [
        "Цены без НДС, за базовую единицу; позиции в «упаковках/комплектах» сравниваются только с такой же ед. изм.",
        f"Группы ЕНС ТРУ с < {MIN_GROUP} позициями исключены ({small_groups} групп) — для них средняя ненадёжна.",
        *([] if include_services else ["Услуги/работы с ед. изм. «услуга»/«работа» исключены: их «цена за единицу» — "
                                       "это сумма договора, сравнение некорректно (include_services=true — включить)."]),
        "Отклонение — индикатор риска, а не доказательство нарушения: различия в характеристиках товара "
        "внутри одного кода ЕНС ТРУ не учитываются.",
    ]
    ev = Evidence(
        tool="find_price_anomalies", question_type="anomaly",
        params={"threshold_pct": threshold_pct, "direction": direction, "include_services": include_services, "enstru_code": enstru_code,
                "customer_bin": customer_bin, "year": year, "text": text, "period": year or "2024–2026",
                "bins": customer_bin or "все 27 БИН"},
        method=METHOD,
        summary={"n_flagged": len(flagged), "share_flagged_pct": len(flagged) / analysed * 100 if analysed else None,
                 "total_overpayment_vs_wavg": total_over,
                 "max_deviation_pct": max((f["deviation_pct"] for f in flagged), default=None),
                 "n_flagged_iqr_confirmed": sum(1 for f in flagged if f["iqr_outlier"])},
        sample={"n_positions_analysed": analysed, "n_enstru_groups": n_groups_ok,
                "n_positions_in_groups": sum(len(g) for g in groups.values() if len(g) >= MIN_GROUP)},
        confidence=confidence_level(analysed, None, [f"групп ЕНС ТРУ с достаточной выборкой: {n_groups_ok}"]),
        limitations=lim,
        items=flagged[:top_k],
        columns=["contract_number", "customer_name", "item_name", "enstru_code", "unit_price", "weighted_avg_adj",
                 "deviation_pct", "qty", "overpayment"],
    )
    return ev.to_dict()
