import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, streamChatMessage } from '../api/client'
import { renderMessageWithCitations } from '../lib/citations'
import type { ChatMessage, ChatSession } from '../types'

interface DraftMessage {
  role: 'user' | 'assistant'
  content: string
  citations: ChatMessage['citations']
  streaming?: boolean
}

export function Chat() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [messages, setMessages] = useState<DraftMessage[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const hasAutoSelected = useRef(false)

  function loadSessions() {
    api
      .get<ChatSession[]>('/api/chat/sessions')
      .then((list) => {
        setSessions(list)
        if (!hasAutoSelected.current && list.length > 0 && list[0].id !== null) {
          hasAutoSelected.current = true
          setActiveId(list[0].id)
        }
      })
      .catch(() => setSessions([]))
  }

  useEffect(loadSessions, [])

  useEffect(() => {
    if (activeId === null) {
      setMessages([])
      return
    }
    api
      .get<ChatMessage[]>(`/api/chat/sessions/${activeId}/messages`)
      .then((list) =>
        setMessages(list.map((m) => ({ role: m.role === 'user' ? 'user' : 'assistant', content: m.content, citations: m.citations }))),
      )
      .catch(() => setMessages([]))
  }, [activeId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function newSession() {
    const session = await api.post<ChatSession>('/api/chat/sessions', { title: null })
    setSessions((prev) => [session, ...prev])
    setActiveId(session.id)
  }

  async function handleSend(e: FormEvent) {
    e.preventDefault()
    const content = input.trim()
    if (!content || sending) return

    let sessionId = activeId
    if (sessionId === null) {
      const session = await api.post<ChatSession>('/api/chat/sessions', { title: null })
      setSessions((prev) => [session, ...prev])
      sessionId = session.id
      setActiveId(sessionId)
    }
    if (sessionId === null) return

    setInput('')
    setError(null)
    setSending(true)
    setMessages((prev) => [
      ...prev,
      { role: 'user', content, citations: [] },
      { role: 'assistant', content: '', citations: [], streaming: true },
    ])

    await streamChatMessage(sessionId, content, {
      onToken: (chunk) => {
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.streaming) next[next.length - 1] = { ...last, content: last.content + chunk }
          return next
        })
      },
      onDone: (payload) => {
        setMessages((prev) => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last?.streaming) {
            next[next.length - 1] = { role: 'assistant', content: payload.content, citations: payload.citations }
          }
          return next
        })
        setSending(false)
        loadSessions()
      },
      onError: (detail) => {
        setError(detail)
        setMessages((prev) => prev.filter((m) => !m.streaming))
        setSending(false)
      },
    })
  }

  return (
    <div className="mx-auto flex h-[calc(100vh-57px)] max-w-6xl gap-4 px-4 py-4">
      <aside className="card flex w-56 shrink-0 flex-col gap-1 overflow-y-auto p-2">
        <button type="button" className="btn-secondary mb-2 w-full" onClick={() => void newSession()}>
          + New chat
        </button>
        {sessions.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setActiveId(s.id)}
            className={`truncate rounded-lg px-2.5 py-2 text-left text-sm ${
              s.id === activeId ? 'bg-surface-overlay text-slate-100' : 'text-slate-400 hover:bg-surface-raised'
            }`}
          >
            {s.title || `Session ${s.id}`}
          </button>
        ))}
        {sessions.length === 0 && <p className="px-2 py-2 text-xs text-slate-500">No sessions yet.</p>}
      </aside>

      <section className="flex flex-1 flex-col gap-3">
        <div className="card flex flex-1 flex-col gap-3 overflow-y-auto p-4">
          {messages.length === 0 && <p className="text-sm text-slate-500">Ask something about your saved library.</p>}
          {messages.map((m, idx) => (
            <div key={idx} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[75%] rounded-xl px-3.5 py-2.5 text-sm whitespace-pre-wrap ${
                  m.role === 'user' ? 'bg-accent text-slate-950' : 'bg-surface-overlay text-slate-100'
                }`}
              >
                {m.content
                  ? renderMessageWithCitations(m.content, m.citations)
                  : m.streaming && <span className="text-slate-400">…</span>}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {error && <p className="card border-red-900/60 bg-red-950/40 px-4 py-2 text-sm text-red-300">{error}</p>}

        <form onSubmit={(e) => void handleSend(e)} className="flex gap-2">
          <input
            className="input"
            placeholder="Ask about your library…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={sending}
          />
          <button type="submit" className="btn-primary" disabled={sending || !input.trim()}>
            {sending ? 'Sending…' : 'Send'}
          </button>
        </form>
      </section>
    </div>
  )
}
