"""Unit-тесты без БД/LLM: fact-check, защита SQL, рендер, статистика."""
from app.agent.agent import fact_check
from app.agent.render import render
from app.agent.tools import run_sql
from app.analytics.common import confidence_level, mad, quantiles

EV = [{"tool": "assess_fair_price", "summary": {"fair_price": 251.2812, "deviation_pct": 342.1395, "total": 921941252.33},
       "sample": {"n_comparables": 26}, "items": [{"qty": 1000.0}]}]


def test_fact_check_accepts_rounded_numbers():
    assert fact_check("Цена выше Fair Price 251.28 тг на 342% (N=26), сумма 921,9 млн тг", EV, "") == []


def test_fact_check_rejects_invented_numbers():
    assert fact_check("отклонение 401%", EV, "") == ["401"]
    assert fact_check("999 договоров", EV, "") == ["999"]          # 999 ≠ 1000 для целых
    assert fact_check("сумма 0,41 млрд", EV, "") == ["0,41 млрд"]


def test_fact_check_ignores_years_and_question_numbers():
    assert fact_check("в 2025 году по лоту 82166119", EV, "лот 82166119-ЗЦП2") == []


def test_run_sql_rejects_writes():
    for bad in ["delete from mart.price_obs", "select 1; drop table x", "update core.lot set qty=0",
                "with x as (select 1) delete from y"]:
        assert "error" in run_sql(bad)


def test_render_has_all_required_sections():
    ev = {"tool": "find_price_anomalies", "question_type": "anomaly", "params": {"period": "2025", "bins": "все 27 БИН"},
          "method": "Δ = P/WAvg − 1", "summary": {"n_flagged": 3}, "sample": {"n_positions_analysed": 100},
          "confidence": {"level": "высокая", "reasons": ["N = 100"]}, "limitations": ["x"],
          "items": [{"contract_number": "1", "contract_url": "https://goszakup.gov.kz/ru/egzcontract/cpublic/show/1"}],
          "columns": ["contract_number"]}
    a = render("q", {"question_type": "anomaly", "language": "ru", "verdict": "3 аномалии"}, [ev])
    for key in ("verdict", "data", "analytics", "metric", "confidence", "top_k"):
        assert key in a["sections"]
    for h in ("Краткий вывод", "Использованные данные", "Сравнение и аналитика", "Метрика оценки",
              "Ограничения и уверенность", "Примеры (Top-K)", "goszakup.gov.kz"):
        assert h in a["markdown"]


def test_render_kazakh_headings():
    a = render("q", {"question_type": "search", "language": "kk", "verdict": "x"}, [])
    assert "Қысқаша қорытынды" in a["markdown"]


def test_stats():
    assert quantiles([1, 2, 3, 4, 5]) == (2, 3, 4)
    assert mad([1, 2, 3, 4, 100]) == 1
    assert confidence_level(30, 0.3)["level"] == "высокая"
    assert confidence_level(10, 0.3)["level"] == "средняя"
    assert confidence_level(3, None)["level"] == "низкая"


def test_fact_check_accepts_truncated_integers():
    ev = [{"summary": {"total_overpayment_vs_wavg": 2908352108.5381}}]
    assert fact_check("сумма отклонения 2908352108 тенге", ev, "") == []
    assert fact_check("сумма отклонения 2908352110 тенге", ev, "") == ["2908352110"]
