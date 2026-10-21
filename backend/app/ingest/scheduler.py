"""Планировщик инкрементальной синхронизации: OWS → raw → core/mart.

Интервал SYNC_INTERVAL_SECONDS (по умолчанию 1 ч) гарантирует задержку ≤ 24 ч от публикации на портале.
В Kubernetes вместо этого процесса используется CronJob (infra/k8s/cronjob-sync.yaml).
"""
import asyncio
import logging
import time

from app.config import settings
from app.etl.build import build
from app.ingest.jobs import run

log = logging.getLogger("scheduler")


def tick():
    try:
        stats = asyncio.run(run("incremental"))
        log.info("sync ok: %s", {k: v for k, v in stats.items() if k != "bins"})
        build()
    except Exception:  # noqa: BLE001 — следующая попытка в следующем цикле
        log.exception("sync failed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    while True:
        tick()
        time.sleep(settings.sync_interval_seconds)
