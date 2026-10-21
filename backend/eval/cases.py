"""Эталонный набор вопросов для оценки точности извлечения фактов (цифры, даты, БИН, ID).

Эталон (ground truth) вычисляется НЕЗАВИСИМЫМИ SQL-запросами к хранилищу — отдельно от аналитических
функций агента, — поэтому оценка проверяет и расчёты, и то, как агент их выбирает и передаёт в ответ.
Факт: {"name", "sql", "kind": number|text, "where": verdict|any}; sql возвращает одно значение.
"""

LOO_ANOMALIES = """
WITH g AS (
  SELECT *, sum(unit_price_adj*qty_base) OVER w AS s, sum(qty_base) OVER w AS q, count(*) OVER w AS n
  FROM mart.price_obs
  WHERE enstru_code IS NOT NULL AND base_unit IS NOT NULL AND base_unit NOT IN ('услуга','работа')
  WINDOW w AS (PARTITION BY enstru_code, base_unit)
)
SELECT count(*) FROM g
WHERE n >= 5 AND q - qty_base > 0 {extra}
  AND unit_price_adj / ((s - unit_price_adj*qty_base) / (q - qty_base)) - 1 > 0.30
"""

CASES = [
    {"id": "sum_customer_2025", "type": "analytics",
     "q": "Какова общая сумма договоров заказчика с БИН 990740002243 за 2025 год и сколько это договоров?",
     "facts": [
         {"name": "total_sum", "kind": "number", "sql": "SELECT sum(total_sum) FROM mart.price_obs WHERE customer_bin='990740002243' AND fin_year=2025"},
         {"name": "n_contracts", "kind": "number", "sql": "SELECT count(DISTINCT contract_id) FROM mart.price_obs WHERE customer_bin='990740002243' AND fin_year=2025"},
         {"name": "bin", "kind": "text", "sql": "SELECT '990740002243'"},
     ]},
    {"id": "sum_customer_2025_kk", "type": "analytics",
     "q": "БСН 990740002243 тапсырыс берушінің 2025 жылғы шарттарының жалпы сомасы қанша?",
     "facts": [
         {"name": "total_sum", "kind": "number", "sql": "SELECT sum(total_sum) FROM mart.price_obs WHERE customer_bin='990740002243' AND fin_year=2025"},
     ]},
    {"id": "count_contracts_2024", "type": "analytics",
     "q": "Сколько договоров заключил заказчик 020440003656 в 2024 году и на какую сумму?",
     "facts": [
         {"name": "n_contracts", "kind": "number", "sql": "SELECT count(DISTINCT contract_id) FROM mart.price_obs WHERE customer_bin='020440003656' AND fin_year=2024"},
         {"name": "total_sum", "kind": "number", "sql": "SELECT sum(total_sum) FROM mart.price_obs WHERE customer_bin='020440003656' AND fin_year=2024"},
     ]},
    {"id": "compare_two", "type": "comparison",
     "q": "Сравни суммы договоров заказчиков 990740002243 и 020440003656 за 2025 год",
     "facts": [
         {"name": "sum_a", "kind": "number", "sql": "SELECT sum(total_sum) FROM mart.price_obs WHERE customer_bin='990740002243' AND fin_year=2025"},
         {"name": "sum_b", "kind": "number", "sql": "SELECT sum(total_sum) FROM mart.price_obs WHERE customer_bin='020440003656' AND fin_year=2025"},
     ]},
    {"id": "top_customer_2026", "type": "comparison",
     "q": "Какой заказчик потратил больше всего по договорам в 2026 году?",
     "facts": [
         {"name": "top_bin", "kind": "text", "sql": "SELECT customer_bin FROM mart.price_obs WHERE fin_year=2026 GROUP BY 1 ORDER BY sum(total_sum) DESC LIMIT 1"},
         {"name": "top_sum", "kind": "number", "sql": "SELECT sum(total_sum) FROM mart.price_obs WHERE fin_year=2026 GROUP BY customer_bin ORDER BY 1 DESC LIMIT 1"},
     ]},
    {"id": "top_supplier_2025", "type": "analytics",
     "q": "Какой поставщик получил больше всего договоров в 2025 году?",
     "facts": [
         {"name": "supplier_bin", "kind": "text", "sql": "SELECT supplier_biin FROM mart.price_obs WHERE fin_year=2025 GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"},
         {"name": "n_positions", "kind": "number", "sql": "SELECT count(*) FROM mart.price_obs WHERE fin_year=2025 GROUP BY supplier_biin ORDER BY 1 DESC LIMIT 1"},
     ]},
    {"id": "diesel_search", "type": "search",
     "q": "Найди договоры на дизельное топливо (ЕНС ТРУ 192026.510.000000) за 2025 год: сколько позиций и на какую сумму?",
     "facts": [
         {"name": "n_positions", "kind": "number", "sql": "SELECT count(*) FROM mart.price_obs WHERE enstru_code LIKE '192026.510.000000%' AND fin_year=2025"},
         {"name": "total_sum", "kind": "number", "sql": "SELECT sum(total_sum) FROM mart.price_obs WHERE enstru_code LIKE '192026.510.000000%' AND fin_year=2025"},
     ]},
    {"id": "diesel_wavg", "type": "analytics",
     "q": "Какова средневзвешенная цена за литр дизельного топлива (ЕНС ТРУ 192026.510.000000) по договорам 2025 года?",
     "facts": [
         {"name": "wavg", "kind": "number", "sql": "SELECT sum(unit_price*qty_base)/sum(qty_base) FROM mart.price_obs WHERE enstru_code LIKE '192026.510.000000%' AND fin_year=2025 AND base_unit='л'"},
     ]},
    {"id": "top_month_2025", "type": "analytics",
     "q": "В каком месяце 2025 года сумма заключённых договоров была максимальной?",
     "facts": [
         {"name": "month", "kind": "text", "sql": "SELECT to_char(month,'YYYY-MM') FROM mart.price_obs WHERE fin_year=2025 AND month >= '2025-01-01' GROUP BY 1 ORDER BY sum(total_sum) DESC LIMIT 1"},
     ]},
    {"id": "lots_count", "type": "analytics",
     "q": "Сколько лотов опубликовал заказчик 000740001307 за всё время в хранилище?",
     "facts": [
         {"name": "n_lots", "kind": "number", "sql": "SELECT count(*) FROM core.lot WHERE customer_bin='000740001307' AND NOT is_deleted"},
     ]},
    {"id": "anomaly_all", "type": "anomaly",
     "q": "Найди закупки с отклонением цены > 30% от средневзвешенной по данному ЕНСТРУ",
     "facts": [
         {"name": "n_flagged", "kind": "number", "sql": LOO_ANOMALIES.format(extra="")},
     ]},
    {"id": "anomaly_2025_kk", "type": "anomaly",
     "q": "2025 жылы бағасы ЕНС ТРУ бойынша орташа өлшемді бағадан 30%-дан астам ауытқыған сатып алуларды тап",
     "facts": [
         {"name": "n_flagged", "kind": "number", "sql": LOO_ANOMALIES.format(extra="AND fin_year = 2025")},
     ]},
    {"id": "anomaly_customer", "type": "anomaly",
     "q": "Найди у заказчика 990740002243 закупки с ценой выше средневзвешенной по ЕНС ТРУ более чем на 30%",
     "facts": [
         {"name": "n_flagged", "kind": "number", "sql": LOO_ANOMALIES.format(extra="AND customer_bin = '990740002243'")},
     ]},
    {"id": "fair_lot_1", "type": "fairness",
     "q": "Оцени адекватность цены лота №82166119-ЗЦП2 относительно аналогичных контрактов других ведомств в идентичном городе поставки",
     "facts": [
         {"name": "target_price", "kind": "number", "sql": "SELECT unit_price FROM mart.price_obs WHERE lot_number='82166119-ЗЦП2' ORDER BY total_sum DESC LIMIT 1"},
         {"name": "n_same_city", "kind": "number", "sql": """
            SELECT count(*) FROM mart.price_obs p, (SELECT enstru_code, base_unit, customer_bin, city_code, lot_id FROM mart.price_obs
                   WHERE lot_number='82166119-ЗЦП2' ORDER BY total_sum DESC LIMIT 1) t
            WHERE p.enstru_code=t.enstru_code AND p.base_unit=t.base_unit AND p.customer_bin<>t.customer_bin
              AND p.city_code=t.city_code AND p.lot_id IS DISTINCT FROM t.lot_id"""},
         {"name": "customer_bin", "kind": "text", "sql": "SELECT customer_bin FROM mart.price_obs WHERE lot_number='82166119-ЗЦП2' LIMIT 1"},
     ]},
    {"id": "fair_lot_2", "type": "fairness",
     "q": "Оцени справедливость цены лота {lot2} в сравнении с контрактами других заказчиков в том же городе",
     "lot_sql": """SELECT p.lot_number FROM mart.price_obs p JOIN mart.enstru_stats s USING (enstru_code, base_unit)
                   WHERE s.n_customers >= 6 AND s.n >= 30 AND p.lot_number IS NOT NULL AND p.fin_year = 2025
                     AND p.city_code = '710000000' ORDER BY p.unit_id LIMIT 1""",
     "facts": [
         {"name": "target_price", "kind": "number", "sql": "SELECT unit_price FROM mart.price_obs WHERE lot_number='{lot2}' ORDER BY total_sum DESC LIMIT 1"},
         {"name": "enstru", "kind": "text", "sql": "SELECT enstru_code FROM mart.price_obs WHERE lot_number='{lot2}' ORDER BY total_sum DESC LIMIT 1"},
     ]},
    {"id": "volume_2026", "type": "volume",
     "q": "Выяви нетипичное завышение количества ТРУ в 2026 году по сравнению с предыдущими годами",
     "facts": [
         {"name": "n_pairs", "kind": "number", "sql": """
            SELECT count(*) FROM (SELECT customer_bin, enstru_code, base_unit FROM mart.volume_yearly
              GROUP BY 1,2,3 HAVING bool_or(year=2026) AND bool_or(year<2026 AND qty_base>0)) x"""},
     ]},
    {"id": "volume_customer", "type": "volume",
     "q": "Есть ли у заказчика 990740002243 нетипичный рост количества закупаемых ТРУ в плане 2026 года относительно 2024–2025?",
     "facts": [
         {"name": "n_pairs", "kind": "number", "sql": """
            SELECT count(*) FROM (SELECT customer_bin, enstru_code, base_unit FROM mart.volume_yearly WHERE customer_bin='990740002243'
              GROUP BY 1,2,3 HAVING bool_or(year=2026) AND bool_or(year<2026 AND qty_base>0)) x"""},
     ]},
]
