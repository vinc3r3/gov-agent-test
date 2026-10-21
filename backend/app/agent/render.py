"""Детерминированная сборка ответа по структуре ТЗ из Evidence (без LLM)."""
from __future__ import annotations

L = {
    "ru": {"verdict": "Краткий вывод", "data": "Использованные данные", "analytics": "Сравнение и аналитика",
           "metric": "Метрика оценки", "conf": "Ограничения и уверенность", "topk": "Примеры (Top-K)",
           "period": "Период", "bins": "БИН заказчиков", "filters": "Фильтры", "n": "Объектов в анализе",
           "level": "Уверенность", "unverified": "Числа в выводе, не найденные в расчётах"},
    "kk": {"verdict": "Қысқаша қорытынды", "data": "Пайдаланылған деректер", "analytics": "Салыстыру және талдау",
           "metric": "Бағалау метрикасы", "conf": "Шектеулер және сенімділік", "topk": "Мысалдар (Top-K)",
           "period": "Кезең", "bins": "Тапсырыс берушілердің БСН", "filters": "Сүзгілер", "n": "Талдаудағы объектілер",
           "level": "Сенімділік", "unverified": "Есептеулерде табылмаған сандар"},
}

LABELS = {
    "target_price": "Цена цели (за ед.)", "planned_price": "Плановая цена лота", "contract_price": "Цена по договору",
    "fair_price": "Fair Price", "fair_range_q1": "Интервал справедливости: от", "fair_range_q3": "Интервал справедливости: до",
    "weighted_avg_comparable": "Средневзвешенная цена аналогов", "deviation_pct": "Отклонение от Fair Price, %",
    "verdict": "Оценка", "outside_iqr": "Вне интервала Q1–Q3", "k_region_target": "Региональный коэффициент",
    "k_season_target": "Сезонный коэффициент", "cpi_factor_target": "Индекс инфляции (к базе)",
    "cpi_base_month": "Базовый месяц ИПЦ", "min_price": "Мин. цена аналогов", "max_price": "Макс. цена аналогов",
    "n_flagged": "Найдено аномалий", "share_flagged_pct": "Доля аномалий, %",
    "total_overpayment_vs_wavg": "Сумма превышения над средневзвешенной, тг", "max_deviation_pct": "Макс. отклонение, %",
    "n_flagged_iqr_confirmed": "Из них подтверждено IQR", "total_extra_amount": "Прирост суммы по аномалиям, тг",
    "max_ratio": "Макс. рост, раз", "n_positions": "Позиций договоров", "n_contracts": "Договоров",
    "n_customers": "Заказчиков", "total_sum": "Сумма, тг", "median_unit_price": "Медианная цена за ед.",
    "weighted_avg_unit_price": "Средневзвешенная цена за ед.", "date_from": "Дата с", "date_to": "Дата по",
    "n_lots_found": "Лотов найдено", "n_groups_shown": "Групп показано", "total_sum_all": "Общая сумма, тг",
    "n_positions_all": "Позиций всего", "n_rows": "Строк",
    "n_comparables": "N аналогов в сравнении", "n_comparables_same_city": "Аналогов в том же городе",
    "n_comparables_national": "Аналогов по РК", "n_positions_analysed": "Позиций проанализировано",
    "n_enstru_groups": "Групп ЕНС ТРУ", "n_positions_in_groups": "Позиций в группах сравнения",
    "n_pairs_analysed": "Пар заказчик×ЕНС ТРУ проанализировано", "n_pairs_with_history_total": "Пар с историей (всего)",
    # колонки Top-K
    "contract_number": "Договор", "customer_name": "Заказчик", "item_name": "Предмет", "enstru_code": "ЕНС ТРУ",
    "enstru_name": "ЕНС ТРУ (название)", "unit_price": "Цена за ед.", "weighted_avg_adj": "Средневзвешенная (ИПЦ)",
    "deviation_pct": "Отклонение, %", "qty": "Кол-во", "overpayment": "Превышение, тг", "city": "Город",
    "sign_date": "Дата", "price_in_target_terms": "Цена в условиях цели", "unit": "Ед. изм.",
    "qty_by_year": "Кол-во по годам", "qty_target_year": "Кол-во в целевом году", "ratio": "Рост, раз",
    "peer_ratio_median": "Рост у других, раз", "extra_amount": "Прирост суммы, тг", "label": "Группа", "key": "Код",
    "share_pct": "Доля, %", "wavg_price": "Средневзвешенная цена", "median_price": "Медианная цена",
    "n_objects": "Объектов", "price_unit": "Ед. изм. для цены", "n_positions_in_price_unit": "Позиций в этой ед. изм.",
}


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, float):
        return f"{v:,.2f}".replace(",", " ") if abs(v) < 1e15 else str(v)
    if isinstance(v, int):
        return f"{v:,}".replace(",", " ")
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_fmt(x)}" for k, x in v.items())
    sv = str(v)
    return sv[:10] if len(sv) >= 19 and sv[4] == "-" and sv[10] in " T" else sv


def current_method(tool: str | None, fallback: str | None) -> str | None:
    """Актуальная формулировка метода инструмента (описание статично и не зависит от данных)."""
    from app.analytics import anomalies, fair_price, volumes
    return {"find_price_anomalies": anomalies.METHOD, "assess_fair_price": fair_price.METHOD,
            "detect_volume_anomalies": volumes.METHOD}.get(tool or "", fallback)


def render(question: str, final: dict, evs: list[dict]) -> dict:
    lang = final.get("language", "ru") if final.get("language") in L else "ru"
    t = L[lang]
    main = evs[-1] if evs else None
    for e in reversed(evs):  # основной — последний не-поисковый аналитический результат
        if e.get("tool") not in ("search_procurements", "run_sql"):
            main = e
            break

    sections: dict = {"verdict": final.get("verdict", "")}
    md = [f"### {t['verdict']}\n{final.get('verdict', '')}"]
    if final.get("unverified_numbers"):
        md.append(f"> ⚠ {t['unverified']}: {', '.join(final['unverified_numbers'])}")

    if main:
        p = main.get("params", {})
        filters = {k: v for k, v in p.items() if k not in ("period", "bins", "target") and v not in (None, "", [])}
        sections["data"] = {"period": p.get("period", "2024–2026"), "bins": p.get("bins", "все 27 БИН"),
                            "filters": filters, "target": p.get("target"),
                            "n_objects": main.get("sample", {}), "sources": [e["tool"] for e in evs]}
        md.append(f"### {t['data']}\n- {t['period']}: {_fmt(p.get('period', '2024–2026'))}\n"
                  f"- {t['bins']}: {_fmt(p.get('bins', 'все 27 БИН'))}\n"
                  + (f"- Объект оценки: {_target_line(p['target'])}\n" if p.get("target") else "")
                  + (f"- {t['filters']}: " + "; ".join(f"{k}={_fmt(v)}" for k, v in filters.items()) + "\n" if filters else "")
                  + "- " + "; ".join(f"{LABELS.get(k, k)}: {_fmt(v)}" for k, v in main.get("sample", {}).items()))
        sections["analytics"] = main.get("summary", {})
        md.append(f"### {t['analytics']}\n" + "\n".join(f"- {LABELS.get(k, k)}: {_fmt(v)}"
                                                        for k, v in main.get("summary", {}).items()))
        sections["metric"] = main.get("method")
        md.append(f"### {t['metric']}\n{main.get('method')}")
        conf = main.get("confidence", {})
        sections["confidence"] = {"level": conf.get("level"), "reasons": conf.get("reasons", []),
                                  "limitations": main.get("limitations", [])}
        md.append(f"### {t['conf']}\n- {t['level']}: **{conf.get('level')}** ({'; '.join(conf.get('reasons', []))})\n"
                  + "\n".join(f"- {x}" for x in main.get("limitations", [])))
        items = main.get("items", [])
        cols = main.get("columns") or (list(items[0].keys())[:6] if items else [])
        sections["top_k"] = {"columns": cols, "items": items}
        if items:
            head = "| # | " + " | ".join(LABELS.get(c, c) for c in cols) + " | Ссылка |\n|" + "---|" * (len(cols) + 2)
            rows = []
            for i, it in enumerate(items, 1):
                link = it.get("contract_url") or it.get("announce_url")
                rows.append(f"| {i} | " + " | ".join(_fmt(it.get(c)) for c in cols)
                            + f" | {'[открыть](' + link + ')' if link else '—'} |")
            md.append(f"### {t['topk']}\n{head}\n" + "\n".join(rows))
    else:
        sections["confidence"] = {"level": "низкая", "reasons": ["нет результатов аналитических инструментов"],
                                  "limitations": []}

    return {"question": question, "question_type": final.get("question_type"), "language": lang,
            "unverified_numbers": final.get("unverified_numbers", []),
            "sections": sections, "markdown": "\n\n".join(md), "evidence": evs}


def _target_line(tg: dict) -> str:
    parts = [tg.get("item_name"), f"лот {tg['lot_number']}" if tg.get("lot_number") else None,
             f"договор {tg['contract_number']}" if tg.get("contract_number") else None,
             f"ЕНС ТРУ {tg.get('enstru_code')}", tg.get("customer_name"), tg.get("city_name")]
    return ", ".join(str(x) for x in parts if x)
