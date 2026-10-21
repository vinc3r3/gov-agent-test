-- MART-слой: витрины для аналитики и AI-агента.
CREATE SCHEMA IF NOT EXISTS mart;

BEGIN;

-- Статусы договоров, цена которых считается состоявшейся (подписан/действует/исполнен/изменён/расторгнут после подписания).
-- Исключены: проекты/согласование (110–165), «Не заключен» (330), отказ/2-й победитель/изменены итоги (410–430),
-- «Передан.Не заключен» (461), недействительные по суду (480).
DROP TABLE IF EXISTS mart.valid_contract_status;
CREATE TABLE mart.valid_contract_status AS
SELECT unnest(ARRAY[185,190,310,315,340,345,350,375,390,450,455,456,460,462,463,464,465,470]) AS status_id;

-- Базовый месяц для приведения цен (последний официальный ИПЦ)
DROP TABLE IF EXISTS mart.cpi_base;
CREATE TABLE mart.cpi_base AS
SELECT month AS base_month, cpi_index AS base_index FROM core.cpi WHERE NOT is_estimate ORDER BY month DESC LIMIT 1;

-- Доминирующая ед. изм. по ЕНС ТРУ (по пунктам плана): используется, если у позиции договора нет пункта плана.
-- Условие: ≥ 80% пунктов плана с этим кодом в одной ед. изм. и ≥ 3 наблюдения.
DROP TABLE IF EXISTS mart.enstru_unit_mode;
CREATE TABLE mart.enstru_unit_mode AS
WITH u AS (
  SELECT enstru_code, unit_code, count(*) AS n, sum(count(*)) OVER (PARTITION BY enstru_code) AS tot
  FROM core.plan_point WHERE enstru_code IS NOT NULL AND unit_code IS NOT NULL GROUP BY 1, 2
)
SELECT DISTINCT ON (enstru_code) enstru_code, unit_code, n, tot, n::numeric / tot AS share
FROM u WHERE tot >= 3 AND n::numeric / tot >= 0.8
ORDER BY enstru_code, n DESC;
CREATE UNIQUE INDEX ON mart.enstru_unit_mode(enstru_code);

-- ---------- Наблюдения цен: позиции договоров (факт) ----------
DROP TABLE IF EXISTS mart.price_obs CASCADE;
CREATE TABLE mart.price_obs AS
WITH x AS (
  SELECT cu.id AS unit_id, c.id AS contract_id, c.contract_number, c.trd_buy_id, c.number_anno,
         cu.lot_id, l.lot_number, cu.plan_point_id,
         c.customer_bin, cs.name_ru AS customer_name, c.supplier_biin, ss.name_ru AS supplier_name,
         coalesce(cu.enstru_code, pp.enstru_code) AS enstru_code,
         coalesce(pp.unit_code, eum.unit_code) AS unit_code,
         CASE WHEN pp.unit_code IS NOT NULL THEN 'plan' WHEN eum.unit_code IS NOT NULL THEN 'enstru_mode' END AS unit_source,
         coalesce(pp.name_ru, l.name_ru) AS item_name,
         coalesce(pp.name_clean, l.name_clean) AS item_name_clean,
         pp.subject_type_id,
         c.trade_method_id,
         c.sign_date, c.fin_year, c.status_id AS contract_status_id,
         cu.item_price, cu.qty, cu.total_sum,
         CASE WHEN pp.kato_code IS NOT NULL THEN pp.kato_code
              WHEN l.kato_code IS NOT NULL THEN l.kato_code
              ELSE cs.kato END AS kato_code,
         CASE WHEN pp.kato_code IS NOT NULL THEN 'plan'
              WHEN l.kato_code IS NOT NULL THEN 'lot'
              WHEN cs.kato IS NOT NULL THEN 'customer' END AS kato_source,
         pp.price AS plan_price
  FROM core.contract_unit cu
  JOIN core.contract c ON c.id = cu.contract_id
  JOIN mart.valid_contract_status v ON v.status_id = c.status_id
  LEFT JOIN core.lot l ON l.id = cu.lot_id
  -- пункт плана: прямая ссылка из позиции договора, иначе — первый пункт плана лота
  LEFT JOIN core.plan_point pp ON pp.id = coalesce(cu.plan_point_id, l.plan_point_ids[1])
  LEFT JOIN core.subject cs ON cs.bin = c.customer_bin
  LEFT JOIN core.subject ss ON ss.bin = c.supplier_biin
  LEFT JOIN mart.enstru_unit_mode eum ON eum.enstru_code = coalesce(cu.enstru_code, pp.enstru_code)
  WHERE NOT cu.is_deleted AND NOT c.is_deleted
    AND c.fin_year BETWEEN 2024 AND 2026
)
SELECT x.*,
       e.name_ru AS enstru_name,
       um.base_unit, um.factor AS unit_factor,
       x.item_price / um.factor AS unit_price,                 -- цена за базовую единицу, без НДС
       x.qty * um.factor AS qty_base,
       date_trunc('month', x.sign_date)::date AS month,
       cpi.cpi_index AS cpi_at_sign,
       -- цена в ценах базового месяца (поправка на инфляцию)
       (x.item_price / um.factor) * b.base_index / cpi.cpi_index AS unit_price_adj,
       left(x.kato_code, 2) AS oblast_code,
       core.kato_city(x.kato_code) AS city_code,
       kc.name_ru AS city_name,
       ko.name_ru AS oblast_name,
       left(x.enstru_code, 2) AS enstru_division,
       'https://goszakup.gov.kz/ru/announce/index/' || x.trd_buy_id AS announce_url,
       'https://goszakup.gov.kz/ru/egzcontract/cpublic/show/' || x.contract_id AS contract_url,
       -- флаги качества
       (x.unit_code IS NULL) AS dq_no_unit,
       (x.kato_code IS NULL) AS dq_no_kato,
       (x.enstru_code IS NULL) AS dq_no_enstru
FROM x
LEFT JOIN core.ref_enstru e ON e.code = x.enstru_code
LEFT JOIN core.unit_map um ON um.unit_code = x.unit_code
-- ИПЦ месяца подписания; вне ряда — ближайший месяц (до 2024 → дек-2023 = 100)
LEFT JOIN LATERAL (
  SELECT coalesce(
    (SELECT c.cpi_index FROM core.cpi c WHERE c.month = date_trunc('month', x.sign_date)::date),
    CASE WHEN x.sign_date < '2024-01-01' THEN 100.0
         ELSE (SELECT c.cpi_index FROM core.cpi c ORDER BY c.month DESC LIMIT 1) END) AS cpi_index
) cpi ON true
LEFT JOIN core.ref_kato kc ON kc.code = core.kato_city(x.kato_code)
LEFT JOIN core.ref_kato ko ON ko.code = left(x.kato_code, 2) || '0000000'
CROSS JOIN mart.cpi_base b
WHERE x.item_price > 0 AND x.qty > 0;

-- нет ед. изм. → цена не нормализуется; такие позиции не участвуют в ценовых сравнениях (base_unit IS NULL)
UPDATE mart.price_obs SET unit_factor = 1, unit_price = item_price, qty_base = qty,
       unit_price_adj = item_price * (SELECT base_index FROM mart.cpi_base) / coalesce(cpi_at_sign, (SELECT base_index FROM mart.cpi_base))
WHERE unit_factor IS NULL;

CREATE INDEX ON mart.price_obs(enstru_code, base_unit);
CREATE INDEX ON mart.price_obs(customer_bin);
CREATE INDEX ON mart.price_obs(lot_number);
CREATE INDEX ON mart.price_obs(contract_id);

-- ---------- Статистика по ЕНС ТРУ + ед.изм. ----------
DROP TABLE IF EXISTS mart.enstru_stats CASCADE;
CREATE TABLE mart.enstru_stats AS
SELECT enstru_code, base_unit, max(enstru_name) AS enstru_name,
       count(*) AS n, count(DISTINCT customer_bin) AS n_customers, count(DISTINCT contract_id) AS n_contracts,
       sum(unit_price_adj * qty_base) / nullif(sum(qty_base), 0) AS wavg_price_adj,
       sum(unit_price * qty_base) / nullif(sum(qty_base), 0) AS wavg_price,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY unit_price_adj) AS median_adj,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY unit_price_adj) AS q1_adj,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY unit_price_adj) AS q3_adj,
       min(unit_price_adj) AS min_adj, max(unit_price_adj) AS max_adj
FROM mart.price_obs
WHERE enstru_code IS NOT NULL AND base_unit IS NOT NULL
GROUP BY enstru_code, base_unit;
CREATE UNIQUE INDEX ON mart.enstru_stats(enstru_code, base_unit);

-- ---------- Региональный коэффициент ----------
-- k_raw = median( price_adj / national_median_по_ЕНСТРУ ) по наблюдениям области в разделе КПВЭД (2 знака ЕНС ТРУ);
-- сжатие к 1 при малой выборке: k = 1 + (k_raw - 1) * n / (n + 20); границы [0.7; 1.5].
DROP TABLE IF EXISTS mart.region_coef CASCADE;
CREATE TABLE mart.region_coef AS
WITH r AS (
  SELECT p.enstru_division, p.oblast_code, p.unit_price_adj / s.median_adj AS ratio
  FROM mart.price_obs p JOIN mart.enstru_stats s USING (enstru_code, base_unit)
  WHERE s.n >= 5 AND s.median_adj > 0 AND p.oblast_code IS NOT NULL
)
SELECT enstru_division, oblast_code, count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY ratio) AS k_raw,
       greatest(0.7, least(1.5, 1 + (percentile_cont(0.5) WITHIN GROUP (ORDER BY ratio) - 1) * count(*) / (count(*) + 20.0))) AS k
FROM r GROUP BY 1, 2;
CREATE UNIQUE INDEX ON mart.region_coef(enstru_division, oblast_code);

-- ---------- Сезонный коэффициент (месяц подписания) ----------
-- Аналогично: медиана отношения к национальной медиане по разделу КПВЭД и месяцу; сжатие (n/(n+30)); границы [0.85; 1.15].
DROP TABLE IF EXISTS mart.season_coef CASCADE;
CREATE TABLE mart.season_coef AS
WITH r AS (
  SELECT p.enstru_division, extract(month FROM p.sign_date)::int AS m, p.unit_price_adj / s.median_adj AS ratio
  FROM mart.price_obs p JOIN mart.enstru_stats s USING (enstru_code, base_unit)
  WHERE s.n >= 5 AND s.median_adj > 0
)
SELECT enstru_division, m AS month, count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY ratio) AS k_raw,
       greatest(0.85, least(1.15, 1 + (percentile_cont(0.5) WITHIN GROUP (ORDER BY ratio) - 1) * count(*) / (count(*) + 30.0))) AS k
FROM r GROUP BY 1, 2;
CREATE UNIQUE INDEX ON mart.season_coef(enstru_division, month);

-- ---------- Лоты с ценой за единицу (для вопросов «лот №…») ----------
DROP TABLE IF EXISTS mart.lot_price CASCADE;
CREATE TABLE mart.lot_price AS
SELECT l.id AS lot_id, l.lot_number, l.trd_buy_id, l.number_anno, l.customer_bin, cs.name_ru AS customer_name,
       l.name_ru AS lot_name, l.name_clean, l.qty, l.amount,
       l.amount / nullif(l.qty, 0) AS lot_unit_price_raw,
       pp.enstru_code, e.name_ru AS enstru_name, pp.unit_code, coalesce(um.base_unit, pp.unit_code) AS base_unit,
       coalesce(um.factor, 1) AS unit_factor,
       l.amount / nullif(l.qty, 0) / coalesce(um.factor, 1) AS lot_unit_price,
       coalesce(pp.kato_code, l.kato_code, cs.kato) AS kato_code,
       core.kato_city(coalesce(pp.kato_code, l.kato_code, cs.kato)) AS city_code,
       t.publish_date, t.trade_method_id, l.status_id,
       'https://goszakup.gov.kz/ru/announce/index/' || l.trd_buy_id AS announce_url
FROM core.lot l
LEFT JOIN core.plan_point pp ON pp.id = l.plan_point_ids[1]
LEFT JOIN core.ref_enstru e ON e.code = pp.enstru_code
LEFT JOIN core.unit_map um ON um.unit_code = pp.unit_code
LEFT JOIN core.trd_buy t ON t.id = l.trd_buy_id
LEFT JOIN core.subject cs ON cs.bin = l.customer_bin
WHERE NOT l.is_deleted;
CREATE INDEX ON mart.lot_price(lot_number);
CREATE INDEX ON mart.lot_price(lot_id);

-- ---------- Годовые объёмы (план закупок, актуальные версии) ----------
DROP TABLE IF EXISTS mart.volume_yearly CASCADE;
CREATE TABLE mart.volume_yearly AS
SELECT pp.customer_bin, s.name_ru AS customer_name, pp.enstru_code, max(e.name_ru) AS enstru_name,
       coalesce(um.base_unit, pp.unit_code) AS base_unit, pp.year,
       sum(pp.qty * coalesce(um.factor, 1)) AS qty_base,
       sum(pp.amount) AS amount,
       count(*) AS n_points,
       array_agg(pp.id ORDER BY pp.amount DESC) AS plan_point_ids
FROM core.plan_point pp
LEFT JOIN core.unit_map um ON um.unit_code = pp.unit_code
LEFT JOIN core.ref_enstru e ON e.code = pp.enstru_code
LEFT JOIN core.subject s ON s.bin = pp.customer_bin
WHERE pp.is_active AND NOT pp.is_deleted AND pp.enstru_code IS NOT NULL AND pp.qty > 0
GROUP BY pp.customer_bin, s.name_ru, pp.enstru_code, coalesce(um.base_unit, pp.unit_code), pp.year;
CREATE INDEX ON mart.volume_yearly(customer_bin, enstru_code);

COMMIT;
ANALYZE;
