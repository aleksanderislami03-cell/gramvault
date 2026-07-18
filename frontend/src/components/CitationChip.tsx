import { Link } from 'react-router-dom'

export function CitationChip({ itemId, snippet }: { itemId: number; snippet?: string | null }) {
  return (
    <Link
      to={`/items/${itemId}`}
      title={snippet ?? undefined}
      className="badge mx-0.5 bg-accent-soft text-accent hover:bg-accent/30 no-underline align-middle"
    >
      Item #{itemId}
    </Link>
  )
}
