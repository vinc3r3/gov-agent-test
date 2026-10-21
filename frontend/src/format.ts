export const LABELS: Record<string, string> = {
  target_price: 'Цена цели за ед.', planned_price: 'Плановая цена', contract_price: 'Цена по договору',
  fair_price: 'Fair Price', fair_range_q1: 'Справедливо от (Q1)', fair_range_q3: 'Справедливо до (Q3)',
  weighted_avg_comparable: 'Средневзв. аналогов', deviation_pct: 'Отклонение, %', verdict: 'Оценка',
  outside_iqr: 'Вне Q1–Q3', k_region_target: 'K региона', k_season_target: 'K сезона',
  cpi_factor_target: 'Индекс ИПЦ', cpi_base_month: 'База ИПЦ', min_price: 'Мин. аналог', max_price: 'Макс. аналог',
  n_flagged: 'Аномалий', share_flagged_pct: 'Доля аномалий, %', total_overpayment_vs_wavg: 'Превышение, ₸',
  max_deviation_pct: 'Макс. отклонение, %', n_flagged_iqr_confirmed: 'Подтверждено IQR',
  total_extra_amount: 'Прирост суммы, ₸', max_ratio: 'Макс. рост, ×', n_positions: 'Позиций',
  n_contracts: 'Договоров', n_customers: 'Заказчиков', total_sum: 'Сумма, ₸', median_unit_price: 'Медиана цены',
  weighted_avg_unit_price: 'Средневзв. цена', date_from: 'С', date_to: 'По', n_lots_found: 'Лотов',
  n_groups_shown: 'Групп', total_sum_all: 'Всего, ₸', n_positions_all: 'Позиций всего', n_rows: 'Строк',
  n_comparables: 'N аналогов', n_comparables_same_city: 'В том же городе', n_comparables_national: 'По РК',
  n_positions_analysed: 'Проанализировано', n_enstru_groups: 'Групп ЕНС ТРУ',
  n_positions_in_groups: 'Позиций в группах', n_pairs_analysed: 'Пар заказчик×ТРУ',
  n_pairs_with_history_total: 'Пар с историей', n_objects: 'Объектов',
  contract_number: 'Договор', customer_name: 'Заказчик', item_name: 'Предмет', enstru_code: 'ЕНС ТРУ',
  enstru_name: 'ЕНС ТРУ', unit_price: 'Цена за ед.', weighted_avg_adj: 'Средневзв. (ИПЦ)', qty: 'Кол-во',
  overpayment: 'Превышение, ₸', city: 'Город', sign_date: 'Дата', price_in_target_terms: 'Цена в условиях цели',
  unit: 'Ед.', qty_by_year: 'Кол-во по годам', qty_target_year: 'Кол-во (цел. год)', ratio: 'Рост, ×',
  peer_ratio_median: 'Рост у других, ×', extra_amount: 'Прирост, ₸', label: 'Группа', key: 'Код',
  share_pct: 'Доля, %', wavg_price: 'Средневзв. цена', median_price: 'Медиана цены',
  // параметры запроса (чипы «Использованные данные»)
  years: 'Годы', year: 'Год', metric: 'Метрика', group_by: 'Группировка', text: 'Текст', customer_bins: 'БИН',
  customer_bin: 'БИН', supplier_biin: 'Поставщик', threshold_pct: 'Порог, %', direction: 'Направление',
  include_services: 'Услуги/работы', comparison_scope: 'Выборка', exclude_customer: 'Исключён заказчик',
  ratio_threshold: 'Порог роста, ×', min_extra_amount: 'Мин. прирост, ₸', source: 'Источник', compared_with: 'Сравнение с',
  min_amount: 'Мин. сумма, ₸', order_by: 'Сортировка', sql: 'SQL', lot_number: 'Лот',
}

const VALUE_LABELS: Record<string, string> = {
  up: 'выше средней', down: 'ниже средней', both: 'в обе стороны', same_city: 'тот же город',
  national_region_adjusted: 'вся РК с поправкой на регион', sum: 'сумма', count: 'количество',
  avg_price: 'средняя цена', median_price: 'медианная цена', wavg_price: 'средневзвешенная цена',
  customer: 'заказчик', supplier: 'поставщик', enstru: 'ЕНС ТРУ', month: 'месяц', city: 'город', oblast: 'область',
  trade_method: 'способ закупки',
}
export const valueLabel = (v: unknown) => (typeof v === 'string' && VALUE_LABELS[v]) || fmt(v)

export const TYPE_LABEL: Record<string, string> = {
  search: 'Поиск', comparison: 'Сравнение', analytics: 'Аналитика', anomaly: 'Аномалии цен',
  fairness: 'Справедливость цены', volume: 'Объёмы',
}

const nf = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })

export function fmt(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'boolean') return v ? 'да' : 'нет'
  if (typeof v === 'number') return Number.isInteger(v) && v >= 1990 && v <= 2100 ? String(v) : nf.format(v)   // годы без разрядов
  if (Array.isArray(v)) return v.map(fmt).join(', ')
  if (typeof v === 'object') return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}: ${fmt(x)}`).join('; ')
  const s = String(v)
  return /^\d{4}-\d{2}-\d{2}[ T]\d/.test(s) ? s.slice(0, 10) : s
}

export const label = (k: string) => LABELS[k] ?? k

const dtf = new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })

/** «21.10, 16:03» — для истории и времени обновления данных */
export function fmtDateTime(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? String(v) : dtf.format(d)
}
