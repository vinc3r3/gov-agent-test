import { useEffect, useRef, useState } from 'react'
import type { Answer, Health } from './types'
import { AnswerCard } from './AnswerCard'
import { TYPE_LABEL, fmt, fmtDateTime } from './format'

interface Example { type: string; q: string }
interface HistoryItem { id: string; created_at: string; question: string; question_type: string | null }
interface Turn { id: string; question: string; answer?: Answer; error?: string; pending?: boolean; startedAt?: number }

export default function App() {
  const [q, setQ] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)   // открытый ответ из истории
  const [examples, setExamples] = useState<Example[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const thread = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const busy = turns.some(t => t.pending)

  useEffect(() => {
    fetch('/api/examples').then(r => r.json()).then(setExamples).catch(() => {})
    fetch('/api/health').then(r => r.json()).then(setHealth).catch(() => {})
    loadHistory()
  }, [])

  function loadHistory() {
    fetch('/api/history?limit=20').then(r => r.json()).then(setHistory).catch(() => {})
  }

  function scrollTo(where: 'top' | 'bottom') {
    requestAnimationFrame(() => {
      const el = thread.current
      if (!el) return
      if (el.scrollHeight > el.clientHeight) el.scrollTo({ top: where === 'top' ? 0 : el.scrollHeight, behavior: 'smooth' })
      else el.scrollIntoView({ behavior: 'smooth', block: 'start' })   // мобильная вёрстка: прокручивается страница
    })
  }

  function newChat() {
    if (busy) return
    setTurns([])
    setActiveId(null)
    setQ('')
    input.current?.focus()
  }

  // ответ из истории открывается отдельно, с чистого листа
  async function openPast(h: HistoryItem) {
    if (busy || activeId === h.id) return
    setActiveId(h.id)
    setTurns([{ id: h.id, question: h.question, pending: true, startedAt: Date.now() }])
    try {
      const r = await fetch(`/api/answer/${h.id}`)
      if (!r.ok) throw new Error('Ответ не найден')
      const a: Answer = await r.json()
      setTurns([{ id: h.id, question: h.question, answer: a }])
    } catch (e) {
      setTurns([{ id: h.id, question: h.question, error: e instanceof Error ? e.message : String(e) }])
    }
    scrollTo('top')
  }

  // fresh=true — новый диалог (пример из списка); иначе уточняющий вопрос в текущем диалоге
  async function send(question: string, fresh = false) {
    question = question.trim()
    if (!question || busy) return
    const base = fresh || activeId ? [] : turns
    const id = crypto.randomUUID()
    const context = base.filter(t => t.answer).slice(-3).map(t => ({ question: t.question }))
    setActiveId(null)
    setTurns([...base, { id, question, pending: true, startedAt: Date.now() }])
    setQ('')
    scrollTo(base.length ? 'bottom' : 'top')
    try {
      const r = await fetch('/api/ask', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, history: context }),
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(body.detail ?? `Ошибка сервера (${r.status})`)
      setTurns(ts => ts.map(t => t.id === id ? { ...t, pending: false, answer: body } : t))
      loadHistory()
    } catch (e) {
      const msg = e instanceof TypeError ? 'Сервер недоступен. Проверьте, что API запущен.' : e instanceof Error ? e.message : String(e)
      setTurns(ts => ts.map(t => t.id === id ? { ...t, pending: false, error: msg } : t))
    }
  }

  function retry(t: Turn) {
    setTurns(ts => ts.filter(x => x.id !== t.id))
    send(t.question)
  }

  return (
    <div className="layout">
      <aside className="side">
        <div className="brand">
          <div className="logo">GZ</div>
          <div>
            <div className="brand-title">Goszakup AI-агент</div>
            <div className="muted small">OWS v3 · 27 организаций · 2024–2026</div>
          </div>
        </div>

        <button className="new-chat" onClick={newChat} disabled={busy}>+ Новый диалог</button>

        <details className="side-block" open>
          <summary><div className="side-h">Примеры вопросов</div></summary>
          {examples.map((e, i) => (
            <button key={i} className="example" onClick={() => send(e.q, true)} disabled={busy}>
              <span className={`badge t-${e.type}`}>{TYPE_LABEL[e.type]}</span>
              <span>{e.q}</span>
            </button>
          ))}
        </details>

        {history.length > 0 && (
          <div className="side-block">
            <div className="side-h">История</div>
            {history.map(h => (
              <button key={h.id} className={`history ${activeId === h.id ? 'on' : ''}`}
                      onClick={() => openPast(h)} disabled={busy} title={h.question}>
                <span className={`dot ${h.question_type ? `t-${h.question_type}` : ''}`} />
                <span className="history-q">{h.question}</span>
                <span className="muted small">{fmtDateTime(h.created_at)}</span>
              </button>
            ))}
          </div>
        )}

        {health && (
          <div className="side-block status">
            <div className="side-h">Данные</div>
            <div className="kv"><span>Позиций договоров</span><b>{fmt(health.counts.price_obs)}</b></div>
            <div className="kv"><span>Договоров</span><b>{fmt(health.counts.contracts)}</b></div>
            <div className="kv"><span>Лотов</span><b>{fmt(health.counts.lots)}</b></div>
            <div className="kv"><span>Пунктов плана</span><b>{fmt(health.counts.plan_points)}</b></div>
            <div className="kv"><span>Обновлено</span><b>{fmtDateTime(health.last_successful_sync)}</b></div>
            <div className="kv"><span>LLM</span><b>{health.llm.model}</b></div>
          </div>
        )}
      </aside>

      <main className="main">
        <div className="thread" ref={thread}>
          {turns.length === 0 && (
            <div className="empty">
              <h1>Аналитика госзакупок с объяснимыми ответами</h1>
              <p className="muted">
                Задайте вопрос на русском или казахском или выберите пример слева. Каждый ответ содержит вывод,
                параметры выборки, расчёты, метод, уверенность и ссылки на конкретные договоры на goszakup.gov.kz.
                Все числа считаются SQL/Python, а не языковой моделью.
              </p>
            </div>
          )}
          {activeId && turns.length > 0 && (
            <div className="turn notice muted small">Сохранённый ответ из истории · вопрос в поле ниже начнёт новый диалог</div>
          )}
          {turns.map(t => (
            <div key={t.id} className="turn">
              <div className="question">{t.question}</div>
              {t.pending && <Pending startedAt={t.startedAt} fromHistory={activeId === t.id} />}
              {t.error && (
                <div className="card error">
                  <div>{t.error}</div>
                  {activeId !== t.id && <button className="link" onClick={() => retry(t)} disabled={busy}>Повторить</button>}
                </div>
              )}
              {t.answer && <AnswerCard a={t.answer} />}
            </div>
          ))}
        </div>

        <form className="composer" onSubmit={e => { e.preventDefault(); send(q) }}>
          <textarea
            ref={input} value={q} rows={2}
            placeholder="Например: «Оцени адекватность цены лота №…» или «Сатып алулардағы аномалияларды тап…»"
            onChange={e => setQ(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(q) } }}
          />
          <div className="composer-side">
            <button type="submit" disabled={busy || !q.trim()}>{busy ? 'Ждём…' : 'Спросить'}</button>
            <span className="muted small hint">Enter — отправить, Shift+Enter — новая строка</span>
          </div>
        </form>
      </main>
    </div>
  )
}

function Pending({ startedAt, fromHistory }: { startedAt?: number; fromHistory: boolean }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const sec = startedAt ? Math.floor((now - startedAt) / 1000) : 0
  return (
    <div className="card pending">
      <span className="spinner" />
      {fromHistory ? 'Загружаю сохранённый ответ…' : (
        <span>Анализирую данные… <span className="muted">{sec} с · обычно 10–60 с: агент вызывает инструменты и считает в базе</span></span>
      )}
    </div>
  )
}
