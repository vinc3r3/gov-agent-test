-- Роль только для чтения витрин: используется инструментом агента run_sql.
-- Пароль задаётся из AGENT_RO_PASSWORD в app/etl/build.py (ALTER ROLE ... PASSWORD).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
    CREATE ROLE agent_ro LOGIN;
  END IF;
END $$;

ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '15s';
ALTER ROLE agent_ro CONNECTION LIMIT 10;

-- доступ только к core и mart (не к raw с исходными ответами и не к ops с журналом вопросов)
REVOKE ALL ON SCHEMA raw, ops FROM agent_ro;
REVOKE CREATE ON SCHEMA public FROM agent_ro;
GRANT USAGE ON SCHEMA core, mart TO agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA core, mart TO agent_ro;
-- витрины пересоздаются при каждой сборке → права на будущие таблицы
ALTER DEFAULT PRIVILEGES IN SCHEMA core, mart GRANT SELECT ON TABLES TO agent_ro;
