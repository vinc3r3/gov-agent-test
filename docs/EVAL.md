# Оценка точности AI-агента

Вопросов: **17**, получено ответов: **17**. Эталонных фактов в отвеченных вопросах: **27**. Эталон считается независимыми SQL-запросами (`backend/eval/cases.py`). Модель: `gemini-3-flash-preview`, `gemini-3.5-flash-lite`, `gemini-3.6-flash`, `gemini-3.8-flash`.

| Метрика | Значение |
|---|---|
| Точность извлечения фактов (ответ целиком) | **100.0%** |
| Факты, названные в тексте вердикта | 81.5% |
| Классификация типа вопроса (search/comparison/analytics считаются одной группой) | 100.0% |
| Классификация типа вопроса (строго) | 88.2% |
| Ответы с неподтверждёнными числами в вердикте | 0.0% |
| Средняя задержка ответа | 31.4 с |

## По вопросам

| id | Вопрос | Модель | Тип (ожид./факт.) | Факты | Инструменты |
|---|---|---|---|---|---|
| sum_customer_2025 | Какова общая сумма договоров заказчика с БИН 990740002243 за 2025 год и сколько это догово | gemini-3.8-flash | analytics / analytics | ✅ total_sum, ✅ n_contracts, ✅ bin | aggregate, run_sql |
| sum_customer_2025_kk | БСН 990740002243 тапсырыс берушінің 2025 жылғы шарттарының жалпы сомасы қанша? | gemini-3.8-flash | analytics / analytics | ✅ total_sum | aggregate |
| count_contracts_2024 | Сколько договоров заключил заказчик 020440003656 в 2024 году и на какую сумму? | gemini-3.8-flash | analytics / analytics | ✅ n_contracts, ✅ total_sum | aggregate, run_sql, run_sql |
| compare_two | Сравни суммы договоров заказчиков 990740002243 и 020440003656 за 2025 год | gemini-3-flash-preview | comparison / comparison | ✅ sum_a, ✅ sum_b | aggregate |
| top_customer_2026 | Какой заказчик потратил больше всего по договорам в 2026 году? | gemini-3.5-flash-lite | comparison / comparison | ✅ top_bin, ✅ top_sum | aggregate |
| top_supplier_2025 | Какой поставщик получил больше всего договоров в 2025 году? | gemini-3.5-flash-lite | analytics / analytics | ✅ supplier_bin, ✅ n_positions | aggregate |
| diesel_search | Найди договоры на дизельное топливо (ЕНС ТРУ 192026.510.000000) за 2025 год: сколько позиц | gemini-3-flash-preview | search / search | ✅ n_positions, ✅ total_sum | search_procurements |
| diesel_wavg | Какова средневзвешенная цена за литр дизельного топлива (ЕНС ТРУ 192026.510.000000) по дог | gemini-3-flash-preview | analytics / search | ✅ wavg | search_procurements |
| top_month_2025 | В каком месяце 2025 года сумма заключённых договоров была максимальной? | gemini-3-flash-preview | analytics / analytics | ✅ month | aggregate |
| lots_count | Сколько лотов опубликовал заказчик 000740001307 за всё время в хранилище? | gemini-3.5-flash-lite | analytics / search | ✅ n_lots | run_sql |
| anomaly_all | Найди закупки с отклонением цены > 30% от средневзвешенной по данному ЕНСТРУ | gemini-3-flash-preview | anomaly / anomaly | ✅ n_flagged | find_price_anomalies |
| anomaly_2025_kk | 2025 жылы бағасы ЕНС ТРУ бойынша орташа өлшемді бағадан 30%-дан астам ауытқыған сатып алул | gemini-3-flash-preview | anomaly / anomaly | ✅ n_flagged | find_price_anomalies |
| anomaly_customer | Найди у заказчика 990740002243 закупки с ценой выше средневзвешенной по ЕНС ТРУ более чем  | gemini-3.5-flash-lite | anomaly / anomaly | ✅ n_flagged | find_price_anomalies |
| fair_lot_1 | Оцени адекватность цены лота №82166119-ЗЦП2 относительно аналогичных контрактов других вед | gemini-3.8-flash | fairness / fairness | ✅ target_price, ✅ n_same_city, ✅ customer_bin | assess_fair_price |
| fair_lot_2 | Оцени справедливость цены лота 72540644-ЗЦП1 в сравнении с контрактами других заказчиков в | gemini-3.6-flash | fairness / fairness | ✅ target_price, ✅ enstru | assess_fair_price |
| volume_2026 | Выяви нетипичное завышение количества ТРУ в 2026 году по сравнению с предыдущими годами | gemini-3.6-flash | volume / volume | ✅ n_pairs | detect_volume_anomalies |
| volume_customer | Есть ли у заказчика 990740002243 нетипичный рост количества закупаемых ТРУ в плане 2026 го | gemini-3.6-flash | volume / volume | ✅ n_pairs | detect_volume_anomalies |

## История прогонов

1. **До исправлений:** точность фактов 92.6% (25/27), ответов с неподтверждёнными числами 5.9%. Разбор промахов:
   * `lots_count`: агент посчитал объявления вместо лотов. В описании `run_sql` не было таблицы `core.lot` → добавлены `core.lot` с колонками и пояснение «лот ≠ объявление»;
   * `anomaly_customer`: модель сама добавила фильтр «2024 год», которого не было в вопросе → в промпт добавлено правило не придумывать фильтры;
   * fact-check считал выдумкой целое число с отброшенными копейками (2908352108 при 2908352108,54) → для целых чисел допуск расширен до «меньше единицы» (тест `test_fact_check_accepts_truncated_integers`).
2. **После исправлений:** эти два вопроса прогнаны заново, результат в таблицах выше. Остальные ответы не менялись.

Прогон смешанный: из-за бесплатной квоты Gemini (20 запросов в сутки на модель) вопросы распределены между несколькими моделями (колонка «Модель»). Для итогового отчёта рекомендуется перепрогон на одной модели: `python -m eval.run_eval`.
