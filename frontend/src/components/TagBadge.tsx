import type { Tag } from '../types'

const KIND_STYLES: Record<Tag['kind'], string> = {
  auto: 'bg-surface-overlay text-slate-300',
  manual: 'bg-accent-soft text-accent',
  hashtag: 'bg-surface-overlay text-emerald-300',
}

export function TagBadge({ tag }: { tag: Tag }) {
  return (
    <span className={`badge ${KIND_STYLES[tag.kind]}`} title={`${tag.kind} tag`}>
      {tag.kind === 'hashtag' ? `#${tag.name}` : tag.name}
    </span>
  )
}
