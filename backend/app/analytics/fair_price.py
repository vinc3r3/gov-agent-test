"""Fair Price — формализованная «справедливая цена».

Цель — лот или позиция договора с ценой P, кодом ЕНС ТРУ, ед. изм., областью o, городом и месяцем m.

  1. Аналоги — позиции договоров с тем же ЕНС ТРУ и ед. изм., других заказчиков, 2024–2026, валидные статусы.
     Приоритет — тот же город поставки (если аналогов ≥ MIN_CITY); иначе вся РК с региональной корректировкой.
  2. Цена каждого аналога приводится к единым условиям:
       цена по ИПЦ к базовому месяцу / K_рег(область аналога) / K_сез(месяц аналога).
  3. FairPrice = медиана приведённых цен × K_рег(o) × K_сез(m) × ИПЦ(месяц цели) / ИПЦ(база).
     Интервал справедливости — от Q1 до Q3 приведённых цен, пересчитанных в условия цели.
  4. Отклонение = P / FairPrice − 1: до 15% — адекватно, 15–30% — умеренно, больше 30% — значительно.

K_рег и K_сез считаются в витринах mart.region_coef и mart.season_coef со сжатием к 1 при малой выборке.
"""
from __future__ import annotations

from app.analytics.common import Evidence, confidence_level, median, pct, quantiles
from app.db import query

METHOD = ("Fair Price = медиана цен аналогов (тот же ЕНС ТРУ и ед. изм., другие заказчики), приведённых к условиям "
          "цели: по инфляции (ИПЦ), региону (K_рег) и месяцу (K_сез). Отклонение = цена / Fair Price − 1: до 15% — "
          "цена адекватна, 15–30% — умеренное отклонение, больше 30% — значительное. Интервал справедливости — "
          "от Q1 до Q3 приведённых цен аналогов.")

MIN_CITY = 5
MIN_ANY = 3


def _coefs():
    reg = {(r["enstru_division"], r["oblast_code"]): float(r["k"]) for r in query("SELECT * FROM mart.region_coef")}
    sea = {(r["enstru_division"], r["month"]): float(r["k"]) for r in query("SELECT * FROM mart.season_coef")}
    cpi = {r["month"]: float(r["cpi_index"]) for r in query("SELECT month, cpi_index FROM core.cpi")}
    base = query("SELECT base_month, base_index FROM mart.cpi_base")[0]
    return reg, sea, cpi, base["base_month"], float(base["base_index"])


def find_target(lot_number: str | None = None, lot_id: int | None = None,
                contract_id: int | None = None, announce_number: str | None = None) -> list[dict]:
    """Ищет цель: лот (плановая цена) и, если есть, фактическая цена по договору."""
    if contract_id:
        return query("""SELECT 'contract' AS kind, unit_id, contract_id, contract_number, lot_id, lot_number, trd_buy_id,
                               customer_bin, customer_name, enstru_code, enstru_name, base_unit, item_name,
                               unit_price AS price, qty_base AS qty, sign_date AS dt, kato_code, oblast_code, city_code,
                               city_name, announce_url, contract_url
                        FROM mart.price_obs WHERE contract_id = %(c)s ORDER BY total_sum DESC""", {"c": contract_id})
    cond, p = [], {}
    if lot_id:
        cond.append("l.lot_id = %(lid)s"); p["lid"] = lot_id
    if lot_number:
        cond.append("(l.lot_number = %(ln)s OR l.lot_number ILIKE %(lnl)s)"); p["ln"] = lot_number; p["lnl"] = lot_number + "%"
    if announce_number:
        cond.append("(l.number_anno = %(an)s OR l.trd_buy_id::text = split_part(%(an)s, '-', 1))"); p["an"] = announce_number
    if not cond:
        return []
    return query(f"""
        SELECT 'lot' AS kind, l.lot_id, l.lot_number, l.trd_buy_id, l.number_anno, l.customer_bin, l.customer_name,
               l.enstru_code, l.enstru_name, l.base_unit, l.lot_name AS item_name,
               coalesce(po.unit_price, l.lot_unit_price) AS price,
               coalesce(po.qty_base, l.qty * l.unit_factor) AS qty,
               l.lot_unit_price AS planned_price, po.unit_price AS contract_price,
               po.contract_id, po.contract_number, po.contract_url,
               coalesce(po.sign_date, l.publish_date) AS dt,
               l.kato_code, left(l.kato_code, 2) AS oblast_code, l.city_code, k.name_ru AS city_name, l.announce_url
        FROM mart.lot_price l
        LEFT JOIN LATERAL (SELECT * FROM mart.price_obs p WHERE p.lot_id = l.lot_id ORDER BY p.total_sum DESC LIMIT 1) po ON true
        LEFT JOIN core.ref_kato k ON k.code = l.city_code
        WHERE {' OR '.join(cond)}
        ORDER BY l.lot_id DESC LIMIT 20""", p)


def assess_fair_price(lot_number: str | None = None, lot_id: int | None = None, contract_id: int | None = None,
                      announce_number: str | None = None, same_city_only: bool = False, top_k: int = 10) -> dict:
    targets = find_target(lot_number, lot_id, contract_id, announce_number)
    if not targets:
        return {"error": "Цель не найдена в хранилище (проверьте номер лота/договора/объявления). "
                         "Охват: 27 БИН из ТЗ, 2024–2026."}
    targets = [t for t in targets if t.get("enstru_code") and t.get("price")] or targets
    t = targets[0]
    if not t.get("enstru_code"):
        return {"error": f"У лота {t.get('lot_number')} нет связи с пунктом плана/ЕНС ТРУ — сравнение невозможно."}

    reg, sea, cpi, base_month, base_idx = _coefs()
    div = t["enstru_code"][:2]
    t_month = t["dt"].date().replace(day=1) if t.get("dt") else base_month
    t_cpi = cpi.get(t_month, base_idx)
    k_reg_t = reg.get((div, t.get("oblast_code")), 1.0)
    k_sea_t = sea.get((div, t_month.month), 1.0)

    comps = query("""
        SELECT unit_id, contract_id, contract_number, lot_number, trd_buy_id, customer_bin, customer_name, item_name,
               unit_price, unit_price_adj, qty_base, sign_date, month, oblast_code, city_code, city_name,
               announce_url, contract_url
        FROM mart.price_obs
        WHERE enstru_code = %(e)s AND base_unit = %(u)s AND customer_bin <> %(b)s
          AND coalesce(lot_id, -1) <> coalesce(%(lid)s, -2)
    """, {"e": t["enstru_code"], "u": t["base_unit"], "b": t["customer_bin"], "lid": t.get("lot_id")})

    city = [c for c in comps if t.get("city_code") and c["city_code"] == t["city_code"]]
    if len(city) >= MIN_CITY or (same_city_only and city):
        sample, scope = city, "same_city"
    else:
        sample, scope = comps, "national_region_adjusted"
    if same_city_only and not city:
        return {"error": f"Нет контрактов других ведомств по ЕНС ТРУ {t['enstru_code']} в городе поставки "
                         f"({t.get('city_name') or t.get('city_code')}). Всего аналогов по РК: {len(comps)}."}
    if len(sample) < MIN_ANY:
        return {"error": f"Недостаточно аналогов для оценки: найдено {len(sample)} (нужно ≥ {MIN_ANY}) "
                         f"по ЕНС ТРУ {t['enstru_code']} / {t['base_unit']}."}

    # нормализация аналогов к «нейтральной» цене (база ИПЦ, РК-среднее, без сезонности)
    for c in sample:
        k_reg = 1.0 if scope == "same_city" else reg.get((div, c["oblast_code"]), 1.0)
        k_sea = sea.get((div, c["month"].month), 1.0) if c["month"] else 1.0
        c["p_norm"] = float(c["unit_price_adj"]) / k_reg / k_sea
    to_target = (1.0 if scope == "same_city" else k_reg_t) * k_sea_t * t_cpi / base_idx
    norm = sorted(c["p_norm"] for c in sample)
    fair = median(norm) * to_target
    q1, q2, q3 = quantiles(norm)
    lo, hi = q1 * to_target, q3 * to_target
    w = sum(float(c["qty_base"]) for c in sample)
    wavg = sum(c["p_norm"] * float(c["qty_base"]) for c in sample) / w * to_target if w else None
    price = float(t["price"])
    dev = pct(price, fair)
    disp = (q3 - q1) / q2 if q2 else None

    if dev is None:
        verdict = "не определено"
    elif abs(dev) <= 15:
        verdict = "цена адекватна рынку"
    elif abs(dev) <= 30:
        verdict = "умеренное " + ("завышение" if dev > 0 else "занижение")
    else:
        verdict = "значительное " + ("завышение" if dev > 0 else "занижение")
    outside_iqr = price > hi or price < lo

    for c in sample:
        c["price_in_target_terms"] = c["p_norm"] * to_target
    nearest = sorted(sample, key=lambda c: abs(c["price_in_target_terms"] - fair))[:top_k]

    limitations = [
        "Цены сравниваются без НДС, за базовую единицу измерения; фасовка (упаковка/комплект) не нормализуется.",
        "Сравнение только внутри 27 организаций из ТЗ (2024–2026), а не по всему рынку РК.",
    ]
    if scope != "same_city":
        limitations.append(f"В городе поставки найдено только {len(city)} аналогов (< {MIN_CITY}) — "
                           f"использована национальная выборка с региональным коэффициентом K_reg={k_reg_t:.3f}.")
    if t["kind"] == "lot" and not t.get("contract_price"):
        limitations.append("По лоту нет договора — оценивается плановая (выделенная) цена лота.")
    if t_month not in cpi:
        limitations.append("Для месяца цели нет официального ИПЦ — использован базовый индекс.")

    ev = Evidence(
        tool="assess_fair_price", question_type="fairness",
        params={"target": {k: t.get(k) for k in ("kind", "lot_number", "lot_id", "contract_id", "contract_number",
                                                  "trd_buy_id", "customer_bin", "customer_name", "item_name",
                                                  "enstru_code", "enstru_name", "base_unit", "city_code",
                                                  "city_name", "dt", "announce_url", "contract_url")},
                "comparison_scope": scope, "period": "2024–2026", "exclude_customer": t["customer_bin"],
                "enstru_code": t["enstru_code"], "unit": t["base_unit"]},
        method=METHOD,
        summary={"target_price": price, "planned_price": t.get("planned_price"), "contract_price": t.get("contract_price"),
                 "fair_price": fair, "fair_range_q1": lo, "fair_range_q3": hi, "weighted_avg_comparable": wavg,
                 "deviation_pct": dev, "verdict": verdict, "outside_iqr": outside_iqr,
                 "k_region_target": k_reg_t, "k_season_target": k_sea_t, "cpi_factor_target": t_cpi / base_idx,
                 "cpi_base_month": base_month, "min_price": min(norm) * to_target, "max_price": max(norm) * to_target},
        sample={"n_comparables": len(sample), "n_comparables_same_city": len(city), "n_comparables_national": len(comps),
                "n_customers": len({c["customer_bin"] for c in sample}),
                "n_contracts": len({c["contract_id"] for c in sample})},
        confidence=confidence_level(len(sample), disp),
        limitations=limitations,
        items=[{"contract_id": c["contract_id"], "contract_number": c["contract_number"], "trd_buy_id": c["trd_buy_id"],
                "customer_bin": c["customer_bin"], "customer_name": c["customer_name"], "item_name": c["item_name"],
                "city": c["city_name"], "sign_date": c["sign_date"], "unit_price": c["unit_price"],
                "price_in_target_terms": c["price_in_target_terms"], "qty": c["qty_base"],
                "contract_url": c["contract_url"], "announce_url": c["announce_url"]} for c in nearest],
        columns=["contract_number", "customer_name", "city", "sign_date", "unit_price", "price_in_target_terms", "qty"],
    )
    return ev.to_dict()
