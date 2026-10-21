-- CORE-слой: типизированные, очищенные и нормализованные сущности.
-- Полностью пересобирается из raw.* (идемпотентно, ~секунды).
CREATE SCHEMA IF NOT EXISTS core;

-- ---------- Функции очистки ----------
-- Очистка названий лотов/пунктов плана: регистр, кавычки, нумерация, служебные пометки, пробелы.
CREATE OR REPLACE FUNCTION core.clean_name(s text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT nullif(btrim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
           lower(coalesce(s, '')),
           '[«»"“”„''`]', '', 'g'),                               -- кавычки
           '^\s*(лот\s*)?(№|n|#)?\s*\d+[\.\)]\s*', '', 'g'),      -- нумерация "1.", "лот №2)"
           '\((зцп|оцп|ок|тендер|аукцион)[^)]*\)', '', 'g'),      -- служебные пометки способа
           '[\s ]+', ' ', 'g'),                              -- множественные пробелы/nbsp
           '^[\s\.,;:\-]+|[\s\.,;:\-]+$', '', 'g')), '')
$$;

-- "Город" для сравнения в идентичном городе поставки:
-- города респ. значения (Астана 71, Алматы 75, Шымкент 79) — по 2 знакам, остальные — по 4 (город/район).
CREATE OR REPLACE FUNCTION core.kato_city(k text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN k IS NULL OR length(k) < 4 THEN NULL
              WHEN left(k, 2) IN ('71', '75', '79') THEN left(k, 2) || '0000000'
              ELSE left(k, 4) || '00000' END
$$;

CREATE OR REPLACE FUNCTION core.ts(s text) RETURNS timestamp LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN s IS NULL OR s = '' OR s LIKE '0000%' THEN NULL ELSE replace(s, 'T', ' ')::timestamp END
$$;

BEGIN;

-- ---------- Справочники ----------
DROP TABLE IF EXISTS core.ref_units CASCADE;
CREATE TABLE core.ref_units AS
SELECT code, payload->>'name_ru' AS name_ru, payload->>'name_kz' AS name_kz, payload->>'alpha_code' AS alpha_code
FROM raw.refs WHERE ref = 'ref_units';
ALTER TABLE core.ref_units ADD PRIMARY KEY (code);

-- Нормализация единиц измерения: приведение к базовой единице с коэффициентом.
-- Цена за базовую единицу = цена / factor, количество в базовых единицах = кол-во * factor.
DROP TABLE IF EXISTS core.unit_map CASCADE;
CREATE TABLE core.unit_map (unit_code text PRIMARY KEY, base_unit text NOT NULL, factor numeric NOT NULL);
INSERT INTO core.unit_map VALUES
  ('166','кг',1), ('163','кг',0.001), ('168','кг',1000), ('179','кг',1000),     -- масса
  ('112','л',1), ('116','л',10), ('122','л',100), ('113','л',1000),             -- объём
  ('006','м',1), ('018','м',1), ('5156','м',1),                                  -- длина
  ('055','м2',1), ('062','м2',1), ('081','м2',1),                               -- площадь
  ('796','шт',1), ('879','шт',1), ('797','шт',100),                             -- штучные
  ('5114','услуга',1), ('980','работа',1), ('245','кВт·ч',1);
-- Остальные единицы — базовая = сама единица (упаковка, флакон, комплект: размер не нормализуем)
INSERT INTO core.unit_map
SELECT code, coalesce(nullif(alpha_code, ''), name_ru), 1 FROM core.ref_units
ON CONFLICT DO NOTHING;

DROP TABLE IF EXISTS core.ref_kato CASCADE;
CREATE TABLE core.ref_kato AS
SELECT code, payload->>'name_ru' AS name_ru, payload->>'name_kz' AS name_kz,
       payload->>'full_name_ru' AS full_name_ru, (payload->>'level_')::int AS level
FROM raw.refs WHERE ref = 'ref_kato';
ALTER TABLE core.ref_kato ADD PRIMARY KEY (code);

DROP TABLE IF EXISTS core.ref_status CASCADE;
CREATE TABLE core.ref_status AS
SELECT ref, code, payload->>'name_ru' AS name_ru, payload->>'name_kz' AS name_kz
FROM raw.refs WHERE ref NOT IN ('ref_units', 'ref_kato');
ALTER TABLE core.ref_status ADD PRIMARY KEY (ref, code);

-- ЕНС ТРУ: собирается из вложенного RefEnstru пунктов плана
DROP TABLE IF EXISTS core.ref_enstru CASCADE;
CREATE TABLE core.ref_enstru AS
SELECT DISTINCT ON (payload->'RefEnstru'->>'code')
       payload->'RefEnstru'->>'code' AS code,
       payload->'RefEnstru'->>'nameRu' AS name_ru,
       payload->'RefEnstru'->>'nameKz' AS name_kz
FROM raw.plans WHERE payload->'RefEnstru'->>'code' IS NOT NULL
ORDER BY payload->'RefEnstru'->>'code', id DESC;
ALTER TABLE core.ref_enstru ADD PRIMARY KEY (code);

-- ---------- Участники ----------
DROP TABLE IF EXISTS core.subject CASCADE;
CREATE TABLE core.subject AS
SELECT bin,
       (payload->>'pid')::bigint AS pid,
       payload->>'nameRu' AS name_ru, payload->>'nameKz' AS name_kz,
       (payload->>'customer')::int = 1 AS is_customer,
       (payload->>'supplier')::int = 1 AS is_supplier,
       (payload->>'markResident')::int = 1 AS is_resident,
       (payload->>'markSmallEmployer')::int = 1 AS is_small,
       payload->'katoList'->>0 AS kato
FROM raw.subjects;
-- Fallback: участники без карточки в OWS — название из лотов/планов/договоров
INSERT INTO core.subject(bin, name_ru, is_customer)
SELECT DISTINCT ON (bin) bin, name, true FROM (
  SELECT payload->>'customerBin' AS bin, payload->>'customerNameRu' AS name FROM raw.lots
  UNION ALL SELECT payload->>'subjectBiin', payload->>'subjectNameRu' FROM raw.plans
) x WHERE bin IS NOT NULL AND name IS NOT NULL AND NOT EXISTS (SELECT 1 FROM core.subject s WHERE s.bin = x.bin)
ORDER BY bin;
ALTER TABLE core.subject ADD PRIMARY KEY (bin);

-- ---------- Объявления ----------
DROP TABLE IF EXISTS core.trd_buy CASCADE;
CREATE TABLE core.trd_buy AS
SELECT id,
       payload->>'numberAnno' AS number_anno,
       payload->>'nameRu' AS name_ru,
       coalesce(nullif(payload->>'customerBin', ''), payload->>'orgBin') AS customer_bin,
       payload->>'orgBin' AS org_bin,
       (payload->>'refTradeMethodsId')::int AS trade_method_id,
       (payload->>'refBuyStatusId')::int AS buy_status_id,
       (payload->>'refSubjectTypeId')::int AS subject_type_id,
       (payload->>'totalSum')::numeric AS total_sum,
       (payload->>'countLots')::int AS count_lots,
       core.ts(payload->>'publishDate') AS publish_date,
       core.ts(payload->>'itogiDatePublic') AS itogi_date,
       CASE jsonb_typeof(payload->'finYear') WHEN 'array' THEN (payload->'finYear'->>0)::int ELSE (payload->>'finYear')::int END AS fin_year,
       CASE jsonb_typeof(payload->'kato') WHEN 'array' THEN payload->'kato'->>0 ELSE payload->>'kato' END AS kato,
       core.ts(payload->>'lastUpdateDate') AS last_update
FROM raw.trd_buys;
ALTER TABLE core.trd_buy ADD PRIMARY KEY (id);

-- ---------- Пункты плана (актуальная версия) ----------
DROP TABLE IF EXISTS core.plan_point CASCADE;
CREATE TABLE core.plan_point AS
SELECT p.id,
       (payload->>'rootrecordId')::bigint AS rootrecord_id,
       payload->>'subjectBiin' AS customer_bin,
       (payload->>'plnPointYear')::int AS year,
       nullif(payload->>'refEnstruCode', '') AS enstru_code,
       payload->>'refUnitsCode' AS unit_code,
       (payload->>'count')::numeric AS qty,
       (payload->>'price')::numeric AS price,
       (payload->>'amount')::numeric AS amount,
       (payload->>'refMonthsId')::int AS month_id,
       (payload->>'refTradeMethodsId')::int AS trade_method_id,
       (payload->>'refPlnPointStatusId')::int AS status_id,
       (payload->>'refSubjectTypeId')::int AS subject_type_id,
       payload->>'nameRu' AS name_ru,
       core.clean_name(coalesce(payload->>'nameRu', '') || ' ' || coalesce(payload->>'descRu', '')) AS name_clean,
       payload->>'extraDescRu' AS extra_desc_ru,
       k.kato AS kato_code,
       k.place AS delivery_place,
       core.ts(payload->>'dateCreate') AS date_create,
       (payload->>'isActive')::int = 1 AS is_active,
       (payload->>'isDeleted')::int = 1 AS is_deleted
FROM raw.plans p
LEFT JOIN LATERAL (
    SELECT x->>'refKatoCode' AS kato, x->>'fullDeliveryPlaceNameRu' AS place
    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.payload->'PlansKato') = 'array' THEN p.payload->'PlansKato' ELSE '[]' END) x
    WHERE coalesce((x->>'isDeleted')::int, 0) = 0
    ORDER BY (x->>'count')::numeric DESC NULLS LAST LIMIT 1
) k ON true;
ALTER TABLE core.plan_point ADD PRIMARY KEY (id);
CREATE INDEX ON core.plan_point(customer_bin, year);
CREATE INDEX ON core.plan_point(enstru_code);

-- ---------- Лоты ----------
DROP TABLE IF EXISTS core.lot CASCADE;
CREATE TABLE core.lot AS
SELECT l.id,
       payload->>'lotNumber' AS lot_number,
       (payload->>'trdBuyId')::bigint AS trd_buy_id,
       payload->>'trdBuyNumberAnno' AS number_anno,
       payload->>'customerBin' AS customer_bin,
       payload->>'nameRu' AS name_ru,
       core.clean_name(payload->>'nameRu') AS name_clean,
       payload->>'descriptionRu' AS description_ru,
       (payload->>'count')::numeric AS qty,
       (payload->>'amount')::numeric AS amount,
       (payload->>'refLotStatusId')::int AS status_id,
       (payload->>'refTradeMethodsId')::int AS trade_method_id,
       (payload->>'dumping')::int = 1 AS dumping,
       (payload->>'unionLots')::int = 1 AS union_lots,
       (SELECT array_agg(x::bigint) FROM jsonb_array_elements_text(payload->'pointList') x WHERE x::bigint > 0) AS plan_point_ids,
       payload->'plnPointKatoList'->>0 AS kato_code,
       (payload->>'isDeleted')::int = 1 AS is_deleted,
       core.ts(payload->>'lastUpdateDate') AS last_update
FROM raw.lots l;
ALTER TABLE core.lot ADD PRIMARY KEY (id);
CREATE INDEX ON core.lot(trd_buy_id);
CREATE INDEX ON core.lot(lot_number);
CREATE INDEX ON core.lot(customer_bin);

-- ---------- Договоры: последняя версия по rootId ----------
DROP TABLE IF EXISTS core.contract CASCADE;
CREATE TABLE core.contract AS
SELECT DISTINCT ON (coalesce(nullif((payload->>'rootId')::bigint, 0), id))
       id,
       coalesce(nullif((payload->>'rootId')::bigint, 0), id) AS root_id,
       payload->>'contractNumberSys' AS contract_number,
       (payload->>'trdBuyId')::bigint AS trd_buy_id,
       payload->>'trdBuyNumberAnno' AS number_anno,
       payload->>'customerBin' AS customer_bin,
       payload->>'supplierBiin' AS supplier_biin,
       (payload->>'finYear')::int AS fin_year,
       coalesce(core.ts(payload->>'signDate'), core.ts(payload->>'crdate')) AS sign_date,
       (payload->>'contractSum')::numeric AS contract_sum,
       (payload->>'contractSumWnds')::numeric AS contract_sum_wnds,
       (payload->>'faktSum')::numeric AS fakt_sum,
       (payload->>'refContractStatusId')::int AS status_id,
       (payload->>'refContractTypeId')::int AS contract_type_id,
       (payload->>'faktTradeMethodsId')::int AS trade_method_id,
       payload->>'refCurrencyCode' AS currency,
       (payload->>'deleted')::int = 1 AS is_deleted
FROM raw.contracts
ORDER BY coalesce(nullif((payload->>'rootId')::bigint, 0), id), id DESC;
ALTER TABLE core.contract ADD PRIMARY KEY (id);
CREATE INDEX ON core.contract(customer_bin, fin_year);
CREATE INDEX ON core.contract(trd_buy_id);

DROP TABLE IF EXISTS core.contract_unit CASCADE;
CREATE TABLE core.contract_unit AS
SELECT (u->>'id')::bigint AS id,
       c.id AS contract_id,
       nullif((u->>'lotId')::bigint, 0) AS lot_id,
       nullif((u->>'plnPointId')::bigint, 0) AS plan_point_id,
       CASE jsonb_typeof(u->'refEnstruCode') WHEN 'array' THEN u->'refEnstruCode'->>0 ELSE u->>'refEnstruCode' END AS enstru_code,
       (u->>'itemPrice')::numeric AS item_price,
       (u->>'itemPriceWnds')::numeric AS item_price_wnds,
       (u->>'quantity')::numeric AS qty,
       (u->>'totalSum')::numeric AS total_sum,
       coalesce((u->>'deleted')::int, 0) = 1 AS is_deleted
FROM core.contract c
JOIN raw.contracts r ON r.id = c.id
CROSS JOIN LATERAL jsonb_array_elements(r.payload->'ContractUnits') u;
DELETE FROM core.contract_unit a USING core.contract_unit b WHERE a.id = b.id AND a.ctid < b.ctid;
ALTER TABLE core.contract_unit ADD PRIMARY KEY (id);
CREATE INDEX ON core.contract_unit(contract_id);
CREATE INDEX ON core.contract_unit(enstru_code);

COMMIT;
ANALYZE;
