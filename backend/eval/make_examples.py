"""Собирает docs/EXAMPLES.md из реальных ответов агента последнего прогона eval (ops.qa_log).

python -m eval.make_examples
"""
from __future__ import annotations

import json
from pathlib import Path

from app.agent.render import current_method, render
from app.db import query

ROOT = Path(__file__).resolve().parents[2]
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
    out = ["# Примеры ответов AI-агента", "",
           "Реальные ответы агента из прогона оценки (`backend/eval/run_eval.py`). Модель указана у каждого примера. Разделы «Использованные данные», «Сравнение и аналитика», «Метрика», «Уверенность» и "
           "«Top-K» формирует код из результатов инструментов. LLM пишет только «Краткий вывод», и его числа "
           "проходят fact-check. Полный JSON каждого ответа (Evidence, трасса вызовов) хранится в `ops.qa_log` "
           "и доступен через `GET /api/answer/{id}`.", ""]
    for n, (cid, title) in enumerate(PICK, 1):
        r = by_id.get(cid)
        if not r or not r.get("answer_id"):
            continue
        a = query("SELECT answer FROM ops.qa_log WHERE id = %(i)s", {"i": r["answer_id"]})[0]["answer"]
        trace = " → ".join(f"`{t['tool']}({json.dumps(t['input'], ensure_ascii=False)})`" for t in a.get("trace", []))
        evs = [{**e, "method": current_method(e.get("tool"), e.get("method"))} for e in a.get("evidence", [])]
        final = {"question_type": a["question_type"], "language": a["language"],
                 "verdict": a["sections"]["verdict"], "unverified_numbers": a.get("unverified_numbers", [])}
        md = render(a["question"], final, evs)["markdown"]   # числа и вердикт — из сохранённого ответа
        md = md.replace("\n### ", "\n#### ").replace("### ", "#### ", 1)
        out += [f"## {n}. {title}", "", f"> **Вопрос:** {a['question']}", "",
                f"Тип: **{a['question_type']}** · язык: {a['language']} · модель: `{a['llm']['model']}` · время: {a['latency_s']} с · "
                f"ID ответа: `{a['id']}`", "", f"Вызовы инструментов: {trace}", "", md, "", "---", ""]
    (ROOT / "docs" / "EXAMPLES.md").write_text("\n".join(out))
    print("docs/EXAMPLES.md written")


if __name__ == "__main__":
    main()
