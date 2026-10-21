"""Сборка core/mart из raw + контроль качества данных.

python -m app.etl.build
"""
import json
import logging
import time

from psycopg import sql

from app.config import settings
from app.db import connect, run_sql_file

log = logging.getLogger("etl")

STEPS = ["001_raw.sql", "002_cpi.sql", "003_core.sql", "004_mart.sql", "005_roles.sql"]

# Проверки качества: (имя, SQL → одно число, порог, описание). Порог — максимально допустимая доля/кол-во.
DQ_CHECKS = [
    ("contracts_without_units",
     "SELECT count(*)::float / nullif((SELECT count(*) FROM core.contract), 0) FROM core.contract c "
     "WHERE NOT EXISTS (SELECT 1 FROM core.contract_unit u WHERE u.contract_id = c.id)", 0.10,
     "доля договоров без позиций"),
    ("price_obs_no_enstru", "SELECT avg(dq_no_enstru::int) FROM mart.price_obs", 0.05,
     "доля позиций договоров без ЕНС ТРУ"),
    ("price_obs_no_unit", "SELECT avg(dq_no_unit::int) FROM mart.price_obs", 0.15,
     "доля позиций без единицы измерения (нет пункта плана и нет доминирующей ед. изм. ЕНС ТРУ)"),
    ("price_obs_unit_inferred", "SELECT avg((unit_source = 'enstru_mode')::int) FROM mart.price_obs", 0.15,
     "доля позиций, где ед. изм. восстановлена по доминирующей ед. изм. ЕНС ТРУ"),
    ("price_obs_no_kato", "SELECT avg(dq_no_kato::int) FROM mart.price_obs", 0.15,
     "доля позиций без места поставки"),
    ("price_obs_kato_fallback_customer",
     "SELECT avg((kato_source = 'customer')::int) FROM mart.price_obs", 0.30,
     "доля позиций, где место поставки взято из адреса заказчика"),
    ("unit_price_vs_total_mismatch",
     "SELECT avg((abs(item_price * qty - total_sum) / nullif(total_sum, 0) > 0.25)::int) FROM mart.price_obs "
     "WHERE total_sum > 0", 0.30,
     "доля позиций, где цена×кол-во расходится с суммой > 25% (НДС/округления)"),
    ("duplicate_plan_versions_active",
     "SELECT count(*) FROM (SELECT rootrecord_id FROM core.plan_point WHERE is_active AND NOT is_deleted "
     "GROUP BY 1 HAVING count(*) > 1) x", 1000, "rootrecord с >1 активной версией"),
    ("extreme_price_ratio",
     "SELECT avg((p.unit_price_adj > 100 * s.median_adj)::int) FROM mart.price_obs p "
     "JOIN mart.enstru_stats s USING (enstru_code, base_unit) WHERE s.n >= 5", 0.02,
     "доля цен > 100× медианы (вероятные ошибки ед. изм.)"),
    ("data_freshness_hours",
     "SELECT extract(epoch FROM now() - max(finished_at)) / 3600 FROM ops.sync_log WHERE error IS NULL", 24,
     "часов с последней успешной синхронизации (SLA ≤ 24)"),
]


def build() -> dict:
    t0 = time.time()
    for step in STEPS:
        log.info("SQL %s", step)
        run_sql_file(step)
    with connect() as c:
        c.execute(sql.SQL("ALTER ROLE agent_ro PASSWORD {}").format(sql.Literal(settings.agent_ro_password)))
    report = dq_report()
    with connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS ops.dq_report (id bigserial PRIMARY KEY, created_at timestamptz DEFAULT now(), report jsonb)")
        c.execute("INSERT INTO ops.dq_report(report) VALUES (%s)", (json.dumps(report, default=float),))
    log.info("ETL готов за %.1fs", time.time() - t0)
    return report


def dq_report() -> dict:
    out = {"checks": [], "counts": {}}
    with connect() as c:
        for t in ["core.plan_point", "core.lot", "core.trd_buy", "core.contract", "core.contract_unit",
                  "core.subject", "mart.price_obs", "mart.enstru_stats", "mart.lot_price", "mart.volume_yearly"]:
            out["counts"][t] = c.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"]
        for name, sql, thr, desc in DQ_CHECKS:
            try:
                v = c.execute(sql).fetchone()
                v = float(list(v.values())[0] or 0)
                out["checks"].append({"check": name, "value": round(v, 4), "threshold": thr,
                                      "ok": v <= thr, "description": desc})
            except Exception as e:  # noqa: BLE001
                out["checks"].append({"check": name, "error": str(e)[:200], "ok": False, "description": desc})
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    print(json.dumps(build(), ensure_ascii=False, indent=1, default=float))
