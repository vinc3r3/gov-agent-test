"""Ingestion OWS v3 → raw.*

Полная загрузка:  python -m app.ingest.jobs full
Инкремент:        python -m app.ingest.jobs incremental

Охват (обоснование в docs/ARCHITECTURE.md):
  Plans     — пункты годового плана: ЕНС ТРУ, ед. изм., плановая цена, КАТО места поставки
  TrdBuy    — объявления (способ, статус, дата публикации)
  Lots      — лоты (кол-во, сумма, связь с пунктами плана)
  Contract  — договоры + ContractUnits (фактическая цена за единицу и количество)
  Subjects  — заказчики и поставщики
  refs      — справочники (ед. изм., КАТО, способы, статусы)
"""
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from app.config import BINS, YEARS
from app.db import connect, run_sql_file
from app.ingest.client import OwsClient

log = logging.getLogger("ingest")

PLAN_FIELDS = """id rootrecordId subjectBiin subjectNameRu nameRu nameKz descRu descKz extraDescRu extraDescKz
refTradeMethodsId refUnitsCode count price amount refMonthsId refPlnPointStatusId plnPointYear refSubjectTypeId
refEnstruCode refFinsourceId isQvazi dateCreate timestamp refPointTypeId supplyDateRu isActive isDeleted
systemId indexDate
PlansKato { refKatoCode fullDeliveryPlaceNameRu count isActive isDeleted }
RefEnstru { code nameRu nameKz }"""

CONTRACT_FIELDS = """id parentId rootId trdBuyId trdBuyNumberAnno trdBuyNameRu refContractStatusId deleted crdate
lastUpdateDate supplierId supplierBiin contractNumber contractNumberSys customerId customerBin refSubjectTypeId
finYear refContractTypeId refCurrencyCode exchangeRate contractSum contractSumWnds signDate contractEndDate
faktSum faktTradeMethodsId descriptionRu systemId indexDate
ContractUnits { id lotId plnPointId itemPrice itemPriceWnds quantity totalSum totalSumWnds deleted
  refContractStatusId refEnstruCode }"""

LOT_FIELDS = """id lotNumber refLotStatusId lastUpdateDate unionLots count amount nameRu nameKz descriptionRu
descriptionKz customerId customerBin customerNameRu trdBuyNumberAnno trdBuyId dumping refTradeMethodsId
refBuyTradeMethodsId pointList enstruList plnPointKatoList singlOrgSign isConstructionWork isDeleted systemId indexDate"""

TRDBUY_FIELDS = """id numberAnno nameRu nameKz totalSum countLots refTradeMethodsId refSubjectTypeId customerBin
customerNameRu orgBin orgNameRu refBuyStatusId startDate endDate publishDate itogiDatePublic finYear kato
lastUpdateDate systemId indexDate"""

REFS = ["ref_units", "ref_kato", "ref_trade_methods", "ref_buy_status", "ref_lots_status",
        "ref_contract_status", "ref_pln_point_status", "ref_subject_type", "ref_months",
        "ref_finsource", "ref_contract_type"]

UPSERT = {
    "plans": "INSERT INTO raw.plans(id, bin, payload) VALUES (%s,%s,%s) ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, bin=EXCLUDED.bin, fetched_at=now()",
    "contracts": "INSERT INTO raw.contracts(id, bin, payload) VALUES (%s,%s,%s) ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, bin=EXCLUDED.bin, fetched_at=now()",
    "lots": "INSERT INTO raw.lots(id, bin, payload) VALUES (%s,%s,%s) ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, bin=EXCLUDED.bin, fetched_at=now()",
    "trd_buys": "INSERT INTO raw.trd_buys(id, bin, payload) VALUES (%s,%s,%s) ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, fetched_at=now()",
}


def _save(table: str, bin_: str | None, items: list[dict]) -> int:
    with connect() as c, c.cursor() as cur:
        cur.executemany(UPSERT[table], [(it["id"], bin_, json.dumps(it, ensure_ascii=False)) for it in items])
    return len(items)


def _set_state(entity: str, scope: str, watermark: datetime, rows: int):
    with connect() as c:
        c.execute("""INSERT INTO ops.sync_state(entity, scope, watermark, rows_loaded, finished_at)
                     VALUES (%s,%s,%s,%s,now())
                     ON CONFLICT (entity, scope) DO UPDATE
                     SET watermark=EXCLUDED.watermark, rows_loaded=EXCLUDED.rows_loaded, finished_at=now()""",
                  (entity, scope, watermark, rows))


def _get_state(entity: str, scope: str) -> datetime | None:
    with connect() as c:
        r = c.execute("SELECT watermark FROM ops.sync_state WHERE entity=%s AND scope=%s", (entity, scope)).fetchone()
    return r["watermark"] if r else None


async def _load(client: OwsClient, entity: str, table: str, bin_: str, filter_: str, fields: str) -> int:
    n = 0
    async for items in client.gql_paginate(entity, filter_, fields):
        n += await asyncio.to_thread(_save, table, bin_, items)
    return n


async def load_refs(client: OwsClient) -> dict:
    stats = {}
    for ref in REFS:
        rows = []
        async for items in client.rest_paginate(f"/v3/refs/{ref}"):
            rows.extend(items)
        def save(rows=rows, ref=ref):
            with connect() as c, c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO raw.refs(ref, code, payload) VALUES (%s,%s,%s) "
                    "ON CONFLICT (ref, code) DO UPDATE SET payload=EXCLUDED.payload, fetched_at=now()",
                    [(ref, _ref_code(ref, r), json.dumps(r, ensure_ascii=False)) for r in rows])
        await asyncio.to_thread(save)
        stats[ref] = len(rows)
        log.info("ref %s: %d", ref, len(rows))
    return stats


def _ref_code(ref: str, r: dict) -> str:
    if ref == "ref_kato":
        return r.get("te") or f"{r['ab']}{r['cd']}{r['ef']}{r['hij']}"
    return str(r.get("id") if r.get("id") is not None else r.get("code"))


def _camel(d: dict) -> dict:
    """REST (snake_case) → формат GraphQL (camelCase), чтобы core-слой читал одинаково."""
    def conv(k: str) -> str:
        head, *rest = k.split("_")
        return head + "".join(w[:1].upper() + w[1:] for w in rest)
    return {conv(k): v for k, v in d.items()}


def _since_filter(field: str, since: datetime | None) -> str:
    return f', {field}: "{since:%Y-%m-%d}"' if since else ""


async def load_bin(client: OwsClient, bin_: str, incremental: bool) -> dict:
    started = datetime.now(timezone.utc)
    stats = {}

    def since(entity):
        if not incremental:
            return None
        wm = _get_state(entity, bin_)
        return (wm - timedelta(days=1)) if wm else None

    s = since("plans")
    stats["plans"] = sum(await asyncio.gather(*[
        _load(client, "Plans", "plans", bin_,
              f'subjectBiin: "{bin_}", plnPointYear: {y}{_since_filter("timestamp", s)}', PLAN_FIELDS)
        for y in YEARS]))
    s = since("contracts")
    stats["contracts"] = sum(await asyncio.gather(*[
        _load(client, "Contract", "contracts", bin_,
              f'customerBin: "{bin_}", finYear: {y}{_since_filter("lastUpdateDate", s)}', CONTRACT_FIELDS)
        for y in YEARS]))
    s = since("lots")
    stats["lots"] = await _load(client, "Lots", "lots", bin_,
                                f'customerBin: "{bin_}"{_since_filter("lastUpdateDate", s)}', LOT_FIELDS)
    s = since("trd_buys")
    stats["trd_buys"] = await _load(client, "TrdBuy", "trd_buys", bin_,
                                    f'orgBin: "{bin_}"{_since_filter("lastUpdateDate", s)}', TRDBUY_FIELDS)
    for entity, n in stats.items():
        _set_state(entity, bin_, started, n)
    log.info("BIN %s: %s", bin_, stats)
    return stats


async def load_missing_trd_buys(client: OwsClient) -> int:
    """Объявления, где заказчик из списка, а организатор — другая организация."""
    with connect() as c:
        ids = [r["id"] for r in c.execute("""
            SELECT DISTINCT (l.payload->>'trdBuyId')::bigint AS id FROM raw.lots l
            WHERE NOT EXISTS (SELECT 1 FROM raw.trd_buys t WHERE t.id = (l.payload->>'trdBuyId')::bigint)
            UNION
            SELECT DISTINCT (c.payload->>'trdBuyId')::bigint FROM raw.contracts c
            WHERE (c.payload->>'trdBuyId')::bigint > 0
              AND NOT EXISTS (SELECT 1 FROM raw.trd_buys t WHERE t.id = (c.payload->>'trdBuyId')::bigint)
        """).fetchall() if r["id"]]

    async def one(i):
        # REST по id — на порядок быстрее, чем GraphQL-фильтр по id
        d = await client.get(f"/v3/trd-buy/{i}")
        if d.get("id"):
            await asyncio.to_thread(_save, "trd_buys", None, [_camel(d)])
            return 1
        return 0

    return sum(await asyncio.gather(*[one(i) for i in ids]))


async def load_subjects(client: OwsClient) -> int:
    with connect() as c:
        bins = [r["b"] for r in c.execute("""
            SELECT b FROM (
              SELECT unnest(%s::text[]) AS b
              UNION SELECT DISTINCT c.payload->>'supplierBiin' FROM raw.contracts c
            ) x WHERE b IS NOT NULL AND b <> '' AND NOT EXISTS (SELECT 1 FROM raw.subjects s WHERE s.bin = x.b)
        """, (BINS,)).fetchall()]

    async def one(b):
        try:
            d = await client.get(f"/v3/subject/biin/{b}")
        except Exception as e:  # noqa: BLE001 — один участник не должен ронять загрузку
            log.warning("subject %s: %r", b, e)
            return 0
        if isinstance(d, list):
            d = next((x for x in d if isinstance(x, dict)), {})
        if not d.get("bin") and not d.get("iin"):
            return 0
        d = _camel({k: v for k, v in d.items() if k not in ("address", "admins", "branches")})
        def save():
            with connect() as c:
                c.execute("INSERT INTO raw.subjects(bin, payload) VALUES (%s,%s) ON CONFLICT (bin) DO UPDATE "
                          "SET payload=EXCLUDED.payload, fetched_at=now()", (b, json.dumps(d, ensure_ascii=False)))
        await asyncio.to_thread(save)
        return 1

    return sum(await asyncio.gather(*[one(b) for b in bins]))


async def run(mode: str) -> dict:
    run_sql_file("001_raw.sql")
    started = datetime.now(timezone.utc)
    client = OwsClient()
    stats: dict = {}
    try:
        if mode == "full" or not _get_state("plans", BINS[0]):
            stats["refs"] = await load_refs(client)
        if mode != "finish":  # finish — только дозагрузка связанных объявлений и участников
            per_bin = await asyncio.gather(*[load_bin(client, b, mode == "incremental") for b in BINS])
            stats["bins"] = dict(zip(BINS, per_bin))
        stats["missing_trd_buys"] = await load_missing_trd_buys(client)
        stats["subjects"] = await load_subjects(client)
        error = None
    except Exception as e:  # noqa: BLE001 — логируем и пробрасываем
        error = repr(e)
        raise
    finally:
        await client.close()
        with connect() as c:
            c.execute("INSERT INTO ops.sync_log(mode, started_at, finished_at, stats, error) VALUES (%s,%s,now(),%s,%s)",
                      (mode, started, json.dumps(stats), error))
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mode = sys.argv[1] if len(sys.argv) > 1 else "incremental"
    s = asyncio.run(run(mode))
    print(json.dumps({k: v for k, v in s.items() if k != "bins"}, ensure_ascii=False, indent=1))
