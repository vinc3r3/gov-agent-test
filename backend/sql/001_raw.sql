-- RAW-слой: ответы OWS v3 как есть (jsonb), upsert по id.
-- Позволяет пересобрать core/mart без повторной выгрузки и аудировать источник.
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS raw.plans      (id bigint PRIMARY KEY, bin text, payload jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS raw.contracts  (id bigint PRIMARY KEY, bin text, payload jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS raw.lots       (id bigint PRIMARY KEY, bin text, payload jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS raw.trd_buys   (id bigint PRIMARY KEY, bin text, payload jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS raw.subjects   (bin text PRIMARY KEY, payload jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS raw.refs       (ref text, code text, payload jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (ref, code));

CREATE INDEX IF NOT EXISTS raw_plans_bin ON raw.plans(bin);
CREATE INDEX IF NOT EXISTS raw_contracts_bin ON raw.contracts(bin);
CREATE INDEX IF NOT EXISTS raw_lots_bin ON raw.lots(bin);

-- Состояние синхронизации (watermark по сущности и БИН)
CREATE TABLE IF NOT EXISTS ops.sync_state (
    entity      text,
    scope       text,
    watermark   timestamptz,
    rows_loaded bigint,
    finished_at timestamptz,
    PRIMARY KEY (entity, scope)
);

CREATE TABLE IF NOT EXISTS ops.sync_log (
    id          bigserial PRIMARY KEY,
    mode        text,          -- full | incremental
    started_at  timestamptz,
    finished_at timestamptz,
    stats       jsonb,
    error       text
);
