import { useEffect, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, isNotFound, mediaUrl } from '../api/client'
import { TagBadge } from '../components/TagBadge'
import type { Item, MediaFile } from '../types'

const ENRICHMENT_LABEL: Record<Item['enrichment_status'], string> = {
  pending: 'Enrichment pending',
  running: 'Enrichment running…',
  done: 'Enriched',
  failed: 'Enrichment failed',
}

function formatDate(iso: string | null): string {
  if (!iso) return 'Unknown date'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
}

function MediaBlock({ file }: { file: MediaFile }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-hidden rounded-lg bg-black">
        {file.media_type === 'video' ? (
          <video src={mediaUrl(file.file_path)} controls className="max-h-[70vh] w-full" />
        ) : (
          <img src={mediaUrl(file.file_path)} alt="" className="max-h-[70vh] w-full object-contain" />
        )}
      </div>
      {file.vision_caption && (
        <p className="card px-3 py-2 text-sm text-slate-300">
          <span className="label mr-2">AI caption</span>
          {file.vision_caption}
        </p>
      )}
      {file.transcript && (
        <p className="card px-3 py-2 text-sm text-slate-300">
          <span className="label mr-2">Transcript</span>
          {file.transcript}
        </p>
      )}
    </div>
  )
}

export function ItemDetail() {
  const { id } = useParams<{ id: string }>()
  const [item, setItem] = useState<Item | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [tagInput, setTagInput] = useState('')
  const [savingTags, setSavingTags] = useState(false)

  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    api
      .get<Item>(`/api/library/items/${id}`, undefined, controller.signal)
      .then(setItem)
      .catch((err) => {
        if (controller.signal.aborted) return
        setError(isNotFound(err) ? 'Item not found.' : err instanceof Error ? err.message : 'Failed to load item')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [id])

  async function saveTags(nextManualTags: string[]) {
    if (!id) return
    setSavingTags(true)
    try {
      const updated = await api.patch<Item>(`/api/library/items/${id}/tags`, { tags: nextManualTags })
      setItem(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update tags')
    } finally {
      setSavingTags(false)
    }
  }

  function addTag(e: FormEvent) {
    e.preventDefault()
    const name = tagInput.trim()
    if (!name || !item) return
    const manual = item.tags.filter((t) => t.kind === 'manual').map((t) => t.name)
    if (manual.includes(name)) return
    setTagInput('')
    void saveTags([...manual, name])
  }

  function removeTag(name: string) {
    if (!item) return
    const manual = item.tags.filter((t) => t.kind === 'manual' && t.name !== name).map((t) => t.name)
    void saveTags(manual)
  }

  if (loading) return <p className="px-4 py-6 text-sm text-slate-500">Loading…</p>
  if (error) return <p className="mx-auto max-w-3xl px-4 py-6 text-sm text-red-300">{error}</p>
  if (!item) return null

  const manualTags = item.tags.filter((t) => t.kind === 'manual')
  const otherTags = item.tags.filter((t) => t.kind !== 'manual')

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6">
      <Link to="/" className="text-sm text-accent no-underline hover:underline">
        ← Back to gallery
      </Link>

      <div className="flex flex-col gap-4">
        {item.media_files.length === 0 ? (
          <p className="card px-4 py-6 text-center text-sm text-slate-500">No media files on this item.</p>
        ) : (
          item.media_files.map((file) => <MediaBlock key={file.id ?? file.file_path} file={file} />)
        )}
      </div>

      <div className="card flex flex-col gap-3 px-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="badge bg-surface-overlay text-slate-300">{item.media_type}</span>
            <span className="badge bg-surface-overlay text-slate-400">{ENRICHMENT_LABEL[item.enrichment_status]}</span>
          </div>
          {item.permalink && (
            <a href={item.permalink} target="_blank" rel="noreferrer" className="btn-secondary">
              View on Instagram
            </a>
          )}
        </div>

        {item.caption && <p className="whitespace-pre-wrap text-sm text-slate-100">{item.caption}</p>}

        <div className="flex items-center justify-between text-sm text-slate-400">
          <span>
            {item.author ? (
              item.author.profile_url ? (
                <a href={item.author.profile_url} target="_blank" rel="noreferrer" className="text-accent no-underline hover:underline">
                  @{item.author.username}
                </a>
              ) : (
                `@${item.author.username}`
              )
            ) : (
              'Unknown author'
            )}
          </span>
          <span>{formatDate(item.taken_at)}</span>
        </div>

        <div className="flex flex-col gap-2 border-t border-surface-border pt-3">
          <span className="label">Tags</span>
          <div className="flex flex-wrap gap-1.5">
            {otherTags.map((t) => (
              <TagBadge key={`${t.kind}-${t.name}`} tag={t} />
            ))}
            {manualTags.map((t) => (
              <span key={`manual-${t.name}`} className="badge bg-accent-soft text-accent">
                {t.name}
                <button
                  type="button"
                  aria-label={`Remove tag ${t.name}`}
                  className="ml-1.5 text-accent/70 hover:text-accent"
                  onClick={() => removeTag(t.name)}
                  disabled={savingTags}
                >
                  ×
                </button>
              </span>
            ))}
            {otherTags.length === 0 && manualTags.length === 0 && (
              <span className="text-sm text-slate-500">No tags yet.</span>
            )}
          </div>
          <form onSubmit={addTag} className="flex gap-2">
            <input
              className="input"
              placeholder="Add a manual tag…"
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              disabled={savingTags}
            />
            <button type="submit" className="btn-secondary" disabled={savingTags || !tagInput.trim()}>
              Add
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
