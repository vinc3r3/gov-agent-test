"""Нетипичное завышение количества ТРУ относительно предыдущих лет.

Источник: годовые планы закупок (актуальные версии пунктов), mart.volume_yearly.
Для пары «заказчик + ЕНС ТРУ + ед. изм.» и целевого года Y:
  рост               = количество в году Y / медиана количества за прошлые годы (2024–2025);
  рост у коллег      = медиана роста у других заказчиков того же ЕНС ТРУ (если их ≥ 3) — системный тренд;
  скорректированный  = рост / max(рост у коллег, 1) — рост, не объяснимый общим трендом.
Аномалия: рост ≥ порога (по умолчанию 2.0), скорректированный рост ≥ порога и прирост суммы ≥ min_amount.
"""
from __future__ import annotations

from collections import defaultdict

from app.analytics.common import Evidence, confidence_level, median
from app.db import query

METHOD = ("Рост = количество в целевом году / медиана количества за прошлые годы. Рост у коллег = медиана роста "
          "у других заказчиков того же ЕНС ТРУ. Скорректированный рост = рост / max(рост у коллег, 1). "
          "Аномалия — рост и скорректированный рост не ниже порога, а прирост суммы не меньше min_amount.")


def detect_volume_anomalies(year: int = 2026, customer_bin: str | None = None, enstru_code: str | None = None,
                            text: str | None = None, ratio_threshold: float = 2.0, min_amount: float = 1_000_000,
                            top_k: int = 10) -> dict:
    rows = query("""SELECT customer_bin, customer_name, enstru_code, enstru_name, base_unit, year, qty_base, amount,
                           n_points, plan_point_ids
                    FROM mart.volume_yearly WHERE year <= %(y)s""", {"y": year})
    hist: dict[tuple, dict[int, dict]] = defaultdict(dict)
    for r in rows:
        hist[(r["customer_bin"], r["enstru_code"], r["base_unit"])][r["year"]] = r

    ratios: dict[tuple, float] = {}
    for k, h in hist.items():
        if year in h:
            prev = [float(h[y]["qty_base"]) for y in h if y < year and float(h[y]["qty_base"]) > 0]
            if prev:
                ratios[k] = float(h[year]["qty_base"]) / median(prev)
    peer: dict[tuple, list[float]] = defaultdict(list)
    for (b, e, u), r in ratios.items():
        peer[(e, u)].append(r)

    flagged, analysed = [], 0
    for k, r in ratios.items():
        b, e, u = k
        if customer_bin and b != customer_bin:
            continue
        if enstru_code and not e.startswith(enstru_code):
            continue
        cur = hist[k][year]
        if text and text.lower() not in (cur["enstru_name"] or "").lower():
            continue
        analysed += 1
        others = [x for kk, x in ratios.items() if kk[1] == e and kk[2] == u and kk[0] != b]
        r_peer = median(others) if len(others) >= 3 else None
        r_adj = r / max(r_peer or 1.0, 1.0)
        prev_years = {y: hist[k][y] for y in sorted(hist[k]) if y < year}
        base_qty = median([float(v["qty_base"]) for v in prev_years.values()])
        base_amt = median([float(v["amount"]) for v in prev_years.values()])
        extra_amount = float(cur["amount"]) - base_amt
        if r >= ratio_threshold and r_adj >= ratio_threshold and extra_amount >= min_amount:
            flagged.append({
                "customer_bin": b, "customer_name": cur["customer_name"], "enstru_code": e,
                "enstru_name": cur["enstru_name"], "unit": u, "year": year,
                "qty_target_year": float(cur["qty_base"]), "qty_prev_median": base_qty,
                "qty_by_year": {y: float(v["qty_base"]) for y, v in prev_years.items()},
                "ratio": r, "peer_ratio_median": r_peer, "ratio_adj": r_adj, "n_peers": len(others),
                "amount_target_year": float(cur["amount"]), "amount_prev_median": base_amt,
                "extra_amount": extra_amount,
                "plan_point_ids": cur["plan_point_ids"][:5],
            })
    flagged.sort(key=lambda x: x["extra_amount"], reverse=True)
    lim = [
        "Используются годовые планы (актуальная редакция): план 2026 г. может быть ещё не полностью сформирован/исполнен.",
        "История — не более 2 предыдущих лет (2024–2025): при одном годе истории оценка менее устойчива.",
        "Объём сравнивается в базовых ед. изм.; смена единицы измерения между годами (шт → упаковка) может давать ложный рост.",
        "Рост может быть обоснован (новые подразделения, нормативы) — результат является индикатором для проверки.",
    ]
    ev = Evidence(
        tool="detect_volume_anomalies", question_type="volume",
        params={"year": year, "compared_with": f"{year - 2}–{year - 1}", "customer_bin": customer_bin or "все 27 БИН",
                "enstru_code": enstru_code, "text": text, "ratio_threshold": ratio_threshold,
                "min_extra_amount": min_amount, "source": "годовые планы закупок (OWS v3 Plans)"},
        method=METHOD,
        summary={"n_flagged": len(flagged), "total_extra_amount": sum(f["extra_amount"] for f in flagged),
                 "max_ratio": max((f["ratio"] for f in flagged), default=None)},
        sample={"n_pairs_analysed": analysed, "n_pairs_with_history_total": len(ratios)},
        confidence=confidence_level(analysed, None, ["история ≤ 2 лет"]),
        limitations=lim,
        items=flagged[:top_k],
        columns=["customer_name", "enstru_name", "unit", "qty_by_year", "qty_target_year", "ratio",
                 "peer_ratio_median", "extra_amount"],
    )
    return ev.to_dict()
