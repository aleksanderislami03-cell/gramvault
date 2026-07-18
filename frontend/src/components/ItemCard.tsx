import { Link } from 'react-router-dom'
import { mediaUrl } from '../api/client'
import type { Item } from '../types'

const TYPE_BADGE_STYLES: Record<Item['media_type'], string> = {
  photo: 'bg-surface-overlay text-slate-300',
  video: 'bg-accent-soft text-accent',
  reel: 'bg-accent-soft text-accent',
  carousel: 'bg-surface-overlay text-amber-300',
}

function formatDate(iso: string | null): string {
  if (!iso) return 'Unknown date'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function Thumbnail({ item }: { item: Item }) {
  const first = item.media_files[0]
  if (!first) {
    return (
      <div className="flex h-full w-full items-center justify-center text-slate-600">
        <span className="text-xs">No media</span>
      </div>
    )
  }
  if (first.media_type === 'video') {
    return (
      <div className="relative h-full w-full">
        <video src={mediaUrl(first.file_path)} className="h-full w-full object-cover" muted preload="metadata" />
        <div className="absolute inset-0 flex items-center justify-center bg-black/20">
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-black/50 text-white">▶</span>
        </div>
      </div>
    )
  }
  return <img src={mediaUrl(first.file_path)} alt={item.caption ?? ''} className="h-full w-full object-cover" loading="lazy" />
}

export function ItemCard({ item }: { item: Item }) {
  return (
    <Link
      to={`/items/${item.id}`}
      className="card group flex flex-col overflow-hidden no-underline transition-colors hover:border-accent/60"
    >
      <div className="aspect-square w-full overflow-hidden bg-surface-overlay">
        <Thumbnail item={item} />
      </div>
      <div className="flex flex-1 flex-col gap-1.5 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className={`badge ${TYPE_BADGE_STYLES[item.media_type]}`}>{item.media_type}</span>
          {item.media_files.length > 1 && (
            <span className="text-xs text-slate-500">{item.media_files.length} files</span>
          )}
        </div>
        <p className="line-clamp-2 text-sm text-slate-200">{item.caption || <span className="text-slate-500">No caption</span>}</p>
        <div className="mt-auto flex items-center justify-between pt-1 text-xs text-slate-400">
          <span className="truncate">{item.author?.username ?? 'Unknown author'}</span>
          <span>{formatDate(item.taken_at)}</span>
        </div>
      </div>
    </Link>
  )
}
