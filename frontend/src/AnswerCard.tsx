import { useState } from 'react'
import type { Answer } from './types'
import { TYPE_LABEL, fmt, label, valueLabel } from './format'

const LINK_KEYS = ['contract_url', 'announce_url']
const KPI_ORDER = ['verdict', 'deviation_pct', 'fair_price', 'target_price', 'n_flagged', 'total_overpayment_vs_wavg',
  'total_extra_amount', 'n_positions', 'total_sum', 'total_sum_all']

export function AnswerCard({ a }: { a: Answer }) {
  const [tab, setTab] = useState<'answer' | 'trace' | 'json'>('answer')
  const [copied, setCopied] = useState(false)
  function copy() {
    navigator.clipboard?.writeText(a.markdown).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }).catch(() => {})
  }
  const s = a.sections
  const conf = s.confidence
  const summary = s.analytics ?? {}
  const kpis = KPI_ORDER.filter(k => k in summary && summary[k] !== null).slice(0, 4)
  const rest = Object.entries(summary).filter(([k]) => !kpis.includes(k))

  return (
    <div className="card answer">
      <div className="answer-head">
        <span className={`badge t-${a.question_type}`}>{TYPE_LABEL[a.question_type] ?? a.question_type}</span>
        {conf && <span className={`conf conf-${conf.level}`}>Уверенность: {conf.level}</span>}
        <span className="muted small grow">{a.latency_s} с · {a.llm.model}</span>
        <button className="link small" onClick={copy}>{copied ? 'Скопировано' : 'Копировать'}</button>
        <div className="tabs">
          {(['answer', 'trace', 'json'] as const).map(t => (
            <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
              {{ answer: 'Ответ', trace: 'Шаги агента', json: 'Evidence' }[t]}
            </button>
          ))}
        </div>
      </div>

      {tab === 'answer' && <>
        <section>
          <h3>Краткий вывод</h3>
          <p className="verdict">{s.verdict}</p>
        </section>

        {kpis.length > 0 && (
          <div className="kpis">
            {kpis.map(k => (
              <div key={k} className={`kpi ${k === 'deviation_pct' && Number(summary[k]) > 30 ? 'bad' : ''}`}>
                <div className="kpi-v">{fmt(summary[k])}</div>
                <div className="kpi-l">{label(k)}</div>
              </div>
            ))}
          </div>
        )}

        {s.data && (
          <section>
            <h3>Использованные данные</h3>
            <div className="chips">
              <span className="chip">Период: {fmt(s.data.period)}</span>
              <span className="chip">БИН: {fmt(s.data.bins)}</span>
              {Object.entries(s.data.filters ?? {}).map(([k, v]) => <span key={k} className="chip">{label(k)}: {valueLabel(v)}</span>)}
              {Object.entries(s.data.n_objects ?? {}).map(([k, v]) => <span key={k} className="chip strong">{label(k)}: {fmt(v)}</span>)}
            </div>
            {s.data.target && (
              <div className="target">
                <b>Объект оценки:</b> {fmt(s.data.target.item_name)} · лот {fmt(s.data.target.lot_number)}
                {' '}· ЕНС ТРУ {fmt(s.data.target.enstru_code)} · {fmt(s.data.target.customer_name)} · {fmt(s.data.target.city_name)}
                {typeof s.data.target.announce_url === 'string' && <> · <a href={s.data.target.announce_url} target="_blank" rel="noreferrer">объявление</a></>}
              </div>
            )}
          </section>
        )}

        {rest.length > 0 && (
          <section>
            <h3>Сравнение и аналитика</h3>
            <div className="grid-kv">
              {rest.map(([k, v]) => <div key={k} className="kv"><span>{label(k)}</span><b>{fmt(v)}</b></div>)}
            </div>
          </section>
        )}

        {s.metric && (
          <section>
            <h3>Метрика оценки</h3>
            <p className="method">{s.metric}</p>
          </section>
        )}

        {conf && (
          <section>
            <h3>Ограничения и уверенность</h3>
            <ul className="lims">
              {conf.reasons.map((r, i) => <li key={'r' + i}>{r}</li>)}
              {conf.limitations.map((r, i) => <li key={'l' + i} className="muted">{r}</li>)}
            </ul>
          </section>
        )}

        {s.top_k && s.top_k.items.length > 0 && (
          <section>
            <h3>Примеры (Top-{s.top_k.items.length})</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>#</th>{s.top_k.columns.map(c => <th key={c}>{label(c)}</th>)}<th>Ссылки</th></tr>
                </thead>
                <tbody>
                  {s.top_k.items.map((it, i) => (
                    <tr key={i}>
                      <td>{i + 1}</td>
                      {s.top_k!.columns.map(c => <td key={c} className={typeof it[c] === 'number' ? 'num' : ''}>{fmt(it[c])}</td>)}
                      <td className="links">
                        {LINK_KEYS.filter(k => typeof it[k] === 'string').map(k => (
                          <a key={k} href={it[k] as string} target="_blank" rel="noreferrer">
                            {k === 'contract_url' ? 'договор' : 'объявление'}
                          </a>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </>}

      {tab === 'trace' && (
        <ol className="trace">
          {a.trace.map((t, i) => (
            <li key={i}>
              <b>{t.tool}</b> <code>{JSON.stringify(t.input)}</code>
              {t.error ? <div className="err">{t.error}</div> : t.sample && <div className="muted small">{fmt(t.sample)}</div>}
            </li>
          ))}
          <li className="muted small">LLM: {a.llm.provider} / {a.llm.model}, токены: {a.llm.usage.input} → {a.llm.usage.output}</li>
        </ol>
      )}

      {tab === 'json' && <pre className="json">{JSON.stringify(a.evidence, null, 2)}</pre>}
    </div>
  )
}
