import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, isNotImplemented } from '../api/client'
import { ItemCard } from '../components/ItemCard'
import type { Author, Item, ItemListResponse, MediaType, SemanticSearchResponse, Tag } from '../types'

const MEDIA_TYPES: MediaType[] = ['photo', 'video', 'reel', 'carousel']
const PAGE_SIZE = 48

export function Gallery() {
  const [searchParams, setSearchParams] = useSearchParams()
  const author = searchParams.get('author') ?? ''
  const mediaType = (searchParams.get('media_type') as MediaType | null) ?? ''
  const tag = searchParams.get('tag') ?? ''
  const dateFrom = searchParams.get('date_from') ?? ''
  const dateTo = searchParams.get('date_to') ?? ''
  const search = searchParams.get('search') ?? ''
  const page = Number(searchParams.get('page') ?? '1')

  const [searchInput, setSearchInput] = useState(search)
  const [authors, setAuthors] = useState<Author[]>([])
  const [tags, setTags] = useState<Tag[]>([])

  const [items, setItems] = useState<Item[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [searchScores, setSearchScores] = useState<Map<number, { score: number; snippet: string | null }>>(new Map())

  useEffect(() => {
    api
      .get<Author[]>('/api/library/authors')
      .then(setAuthors)
      .catch(() => setAuthors([]))
    api
      .get<Tag[]>('/api/library/tags')
      .then(setTags)
      .catch(() => setTags([]))
  }, [])

  // Keep the search box in sync when navigation changes the URL directly.
  useEffect(() => setSearchInput(search), [search])

  // Debounce the search box -> `search` query param.
  useEffect(() => {
    const handle = setTimeout(() => {
      if (searchInput === search) return
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (searchInput) next.set('search', searchInput)
        else next.delete('search')
        next.delete('page')
        return next
      })
    }, 350)
    return () => clearTimeout(handle)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)

    async function load() {
      try {
        if (search) {
          const res = await api.get<SemanticSearchResponse>(
            '/api/chat/search',
            { q: search, top_k: 60 },
            controller.signal,
          )
          setItems(res.results.map((r) => r.item))
          setTotal(res.results.length)
          setSearchScores(new Map(res.results.map((r) => [r.item.id ?? -1, { score: r.score, snippet: r.snippet }])))
        } else {
          const res = await api.get<ItemListResponse>(
            '/api/library/items',
            {
              author: author || undefined,
              media_type: mediaType || undefined,
              tag: tag || undefined,
              date_from: dateFrom || undefined,
              date_to: dateTo || undefined,
              page,
              page_size: PAGE_SIZE,
            },
            controller.signal,
          )
          setItems(res.items)
          setTotal(res.total)
          setSearchScores(new Map())
        }
      } catch (err) {
        if (controller.signal.aborted) return
        if (isNotImplemented(err)) setError('This feature is not implemented on the backend yet.')
        else setError(err instanceof Error ? err.message : 'Failed to load items')
        setItems([])
        setTotal(0)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }

    void load()
    return () => controller.abort()
  }, [author, mediaType, tag, dateFrom, dateTo, search, page])

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total])

  function updateFilter(key: string, value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value)
      else next.delete(key)
      next.delete('page')
      return next
    })
  }

  function goToPage(n: number) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('page', String(n))
      return next
    })
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6">
      <div className="flex flex-col gap-3">
        <input
          className="input"
          placeholder="Semantic search across captions, transcripts, and vision captions…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
        <div className="flex flex-wrap gap-2">
          <select
            className="input w-auto"
            value={mediaType}
            onChange={(e) => updateFilter('media_type', e.target.value)}
            disabled={!!search}
          >
            <option value="">All types</option>
            {MEDIA_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select
            className="input w-auto"
            value={author}
            onChange={(e) => updateFilter('author', e.target.value)}
            disabled={!!search}
          >
            <option value="">All authors</option>
            {authors.map((a) => (
              <option key={a.username} value={a.username}>
                {a.username}
              </option>
            ))}
          </select>
          <select
            className="input w-auto"
            value={tag}
            onChange={(e) => updateFilter('tag', e.target.value)}
            disabled={!!search}
          >
            <option value="">All tags</option>
            {tags.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
          <input
            type="date"
            className="input w-auto"
            value={dateFrom}
            onChange={(e) => updateFilter('date_from', e.target.value)}
            disabled={!!search}
          />
          <span className="self-center text-slate-500">to</span>
          <input
            type="date"
            className="input w-auto"
            value={dateTo}
            onChange={(e) => updateFilter('date_to', e.target.value)}
            disabled={!!search}
          />
          {(author || mediaType || tag || dateFrom || dateTo || search) && (
            <button type="button" className="btn-ghost" onClick={() => setSearchParams({})}>
              Clear filters
            </button>
          )}
        </div>
      </div>

      {error && <p className="card border-red-900/60 bg-red-950/40 px-4 py-3 text-sm text-red-300">{error}</p>}

      {loading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-500">No items found.</p>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
          {items.map((item) => {
            const meta = item.id !== null ? searchScores.get(item.id) : undefined
            return (
              <div key={item.id} className="flex flex-col gap-1">
                <ItemCard item={item} />
                {search && meta && (
                  <p className="line-clamp-1 px-1 text-xs text-slate-500">
                    match {(meta.score * 100).toFixed(0)}%{meta.snippet ? ` — ${meta.snippet}` : ''}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}

      {!search && totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 pt-2">
          <button type="button" className="btn-secondary" disabled={page <= 1} onClick={() => goToPage(page - 1)}>
            Previous
          </button>
          <span className="text-sm text-slate-400">
            Page {page} of {totalPages}
          </span>
          <button
            type="button"
            className="btn-secondary"
            disabled={page >= totalPages}
            onClick={() => goToPage(page + 1)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  )
}
