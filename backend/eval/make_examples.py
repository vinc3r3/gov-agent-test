"""Собирает docs/EXAMPLES.md из реальных ответов агента последнего прогона eval (ops.qa_log).

python -m eval.make_examples
"""
from __future__ import annotations

import json
from pathlib import Path

from app.agent.render import current_method, render
from app.db import query

ROOT = Path(__file__).resolve().parents[2]
TOP = 5  # строк в таблице примеров
PICK = [  # (id кейса из eval, заголовок)
    ("anomaly_all", "Аномалии: отклонение цены > 30% от средневзвешенной по ЕНС ТРУ"),
    ("fair_lot_1", "Справедливость цены лота относительно других ведомств в том же городе"),
    ("volume_2026", "Объёмы: нетипичное завышение количества ТРУ относительно прошлых лет"),
    ("anomaly_2025_kk", "Аномалии — вопрос на казахском языке"),
    ("compare_two", "Сравнение заказчиков"),
    ("diesel_search", "Поиск по ЕНС ТРУ"),
]


def main() -> None:
    res = json.loads((ROOT / "backend" / "eval" / "results.json").read_text())
    by_id = {r["id"]: r for r in res["results"]}
    out = ["# Примеры ответов", "",
           "Реальные ответы агента из прогона оценки. Вывод пишет LLM, остальные разделы собирает код из расчётов. "
           "Полные ответы с трассой вызовов — `GET /api/answer/{id}`.", ""]
    for n, (cid, title) in enumerate(PICK, 1):
        r = by_id.get(cid)
        if not r or not r.get("answer_id"):
            continue
        a = query("SELECT answer FROM ops.qa_log WHERE id = %(i)s", {"i": r["answer_id"]})[0]["answer"]
        trace = " → ".join(f"`{t['tool']}`" for t in a.get("trace", []))
        evs = [{**e, "method": current_method(e.get("tool"), e.get("method")), "items": (e.get("items") or [])[:TOP]}
               for e in a.get("evidence", [])]
        final = {"question_type": a["question_type"], "language": a["language"],
                 "verdict": a["sections"]["verdict"], "unverified_numbers": a.get("unverified_numbers", [])}
        md = render(a["question"], final, evs)["markdown"]   # числа и вердикт — из сохранённого ответа
        md = md.replace("\n### ", "\n#### ").replace("### ", "#### ", 1)
        out += [f"## {n}. {title}", "", f"> {a['question']}", "",
                f"`{a['llm']['model']}` · {a['latency_s']:.0f} с · инструменты: {trace}", "", md, "", "---", ""]
    (ROOT / "docs" / "EXAMPLES.md").write_text("\n".join(out))
    print("docs/EXAMPLES.md written")


if __name__ == "__main__":
    main()
