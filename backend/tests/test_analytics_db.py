"""Интеграционные тесты на реальном хранилище: аналитика совпадает с независимым SQL (эталон из eval/cases.py)."""
import pytest

from app.db import query
from eval.cases import LOO_ANOMALIES

pytestmark = pytest.mark.skipif(not query("SELECT to_regclass('mart.price_obs') AS t")[0]["t"],
                                reason="хранилище не собрано")


def test_anomalies_match_independent_sql():
    from app.analytics.anomalies import find_price_anomalies
    gt = list(query(LOO_ANOMALIES.format(extra="AND fin_year = 2025"))[0].values())[0]
    assert find_price_anomalies(year=2025)["summary"]["n_flagged"] == gt


def test_search_wavg_matches_sql():
    from app.analytics.search import search_procurements
    gt = query("""SELECT sum(unit_price*qty_base)/sum(qty_base) AS v FROM mart.price_obs
                  WHERE enstru_code LIKE '192026.510.000000%%' AND fin_year=2025 AND base_unit='л'""")[0]["v"]
    r = search_procurements(enstru_code="192026.510.000000", years=[2025])
    assert abs(r["summary"]["weighted_avg_unit_price"] - float(gt)) < 0.01


def test_fair_price_structure_and_bounds():
    from app.analytics.fair_price import assess_fair_price
    r = assess_fair_price(lot_number="82166119-ЗЦП2")
    s = r["summary"]
    assert s["fair_range_q1"] <= s["fair_price"] <= s["fair_range_q3"]
    assert r["sample"]["n_comparables"] >= 5
    assert all(i["customer_bin"] != r["params"]["target"]["customer_bin"] for i in r["items"])


def test_dq_checks_pass():
    from app.etl.build import dq_report
    failed = [c["check"] for c in dq_report()["checks"] if not c["ok"]]
    assert failed == []


def test_agent_ro_role_cannot_write_or_read_raw():
    import psycopg
    from app.config import settings
    with psycopg.connect(settings.database_url_ro, autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM mart.price_obs").fetchone()[0] > 0
        for q in ["DELETE FROM mart.price_obs", "SELECT 1 FROM raw.plans LIMIT 1", "SELECT 1 FROM ops.qa_log LIMIT 1",
                  "SET default_transaction_read_only = off"]:
            try:
                c.execute(q)
                if q.startswith("SET"):
                    c.execute("DELETE FROM mart.price_obs")   # даже вне read-only — нет права DELETE
                raise AssertionError(f"allowed: {q}")
            except psycopg.Error:
                pass
