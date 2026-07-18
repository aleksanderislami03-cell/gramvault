import type { ReactNode } from 'react'
import { CitationChip } from '../components/CitationChip'
import type { ChatCitation } from '../types'

/** Matches the inline citation marker format emitted by the chat backend —
 * see `backend/gramvault/chat/prompt.py` (CITATION_MARKER_REGEX) and
 * `backend/gramvault/chat/service.py::parse_citations`. */
const CITATION_MARKER_REGEX = /\[\[item:(\d+)\]\]/g

/**
 * Split raw assistant text on `[[item:<id>]]` markers and render each one
 * as a clickable `CitationChip` instead of showing the raw marker text.
 * `citations` (if provided) supplies snippets for the chip tooltip.
 */
export function renderMessageWithCitations(text: string, citations: ChatCitation[] = []): ReactNode[] {
  const snippetByItemId = new Map<number, string | null>()
  for (const c of citations) snippetByItemId.set(c.item_id, c.snippet)

  const nodes: ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  const regex = new RegExp(CITATION_MARKER_REGEX)
  let key = 0

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(<span key={key++}>{text.slice(lastIndex, match.index)}</span>)
    }
    const itemId = Number(match[1])
    nodes.push(<CitationChip key={key++} itemId={itemId} snippet={snippetByItemId.get(itemId)} />)
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    nodes.push(<span key={key++}>{text.slice(lastIndex)}</span>)
  }
  return nodes
}
