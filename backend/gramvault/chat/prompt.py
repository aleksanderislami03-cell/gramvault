"""Builds the RAG prompt fed to the chat model: a system instruction plus a
numbered, citable block of retrieved item summaries.

CITATION MARKER FORMAT (stable contract — Agent A5 the frontend parses
this, Agent A7's README should document it too):

    `[[item:<item_id>]]`

    - `<item_id>` is the integer primary key of a row in `items` (the same
      id used by `GET /api/library/items/{item_id}`).
    - The model is instructed to inline this marker directly after any
      claim it draws from a specific saved item, e.g.:
          "You saved a pasta recipe from @chef.mia [[item:42]] last spring."
    - Multiple citations may appear back-to-back: `[[item:42]][[item:17]]`.
    - The frontend should regex-match `\\[\\[item:(\\d+)\\]\\]` in rendered
      assistant messages and replace each match with a clickable citation
      chip linking to that item's detail page. The backend additionally
      persists parsed citations as `chat_citations` rows (see
      `gramvault.chat.service`) so citation data survives without
      re-parsing on every page load.
    - A marker referencing an item_id that wasn't actually retrieved for
      this turn should be treated as a model hallucination by the
      frontend/backend (service.py filters these out — see
      `parse_citations()`).
"""

from __future__ import annotations

from gramvault.chat.retrieval import RetrievalResult
from gramvault.models.schemas import Item

CITATION_MARKER_REGEX = r"\[\[item:(\d+)\]\]"

SYSTEM_INSTRUCTION = """You are GramVault's assistant: a private, local search \
companion over the user's saved Instagram posts, reels, and carousels.

Answer the user's question using ONLY the information in the "Retrieved items" \
section below. Do not use outside knowledge about the people, places, or brands \
mentioned. If the retrieved items don't contain enough information to answer, \
say so plainly instead of guessing.

Whenever you state something drawn from a specific retrieved item, cite it \
inline immediately after the claim using this exact marker format:

    [[item:<item_id>]]

where <item_id> is the numeric ID shown for that item below (e.g. "Item 42" \
means you cite it as [[item:42]]). Cite every item you actually use — do not \
cite items you didn't rely on, and never invent an item_id that isn't listed \
below.
"""

_NO_RESULTS_NOTE = (
    "No retrieved items matched this query. Tell the user you couldn't find "
    "anything relevant in their saved library, and don't fabricate an answer."
)


def _truncate(text: str | None, max_len: int = 300) -> str | None:
    if not text:
        return None
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def build_context_block(items: dict[int, Item], results: list[RetrievalResult]) -> str:
    """Render the numbered "Retrieved items" context section. `results`
    controls ordering (already reranked by `retrieval.hybrid_search`);
    `items` supplies the full Item data (author/tags/media) to summarize."""
    if not results:
        return _NO_RESULTS_NOTE

    lines = ["Retrieved items:"]
    for result in results:
        item = items.get(result.item_id)
        if item is None:
            continue  # id vanished between retrieval and fetch — skip silently

        author = item.author.username if item.author else "unknown author"
        date = item.taken_at.date().isoformat() if item.taken_at else "unknown date"
        tag_names = ", ".join(t.name for t in item.tags) if item.tags else "none"

        caption_excerpt = _truncate(item.caption)
        ai_excerpt = None
        for mf in item.media_files:
            ai_excerpt = _truncate(mf.transcript or mf.vision_caption)
            if ai_excerpt:
                break
        if result.snippet and not caption_excerpt and not ai_excerpt:
            ai_excerpt = _truncate(result.snippet)

        lines.append(f"\nItem {item.id} — @{author}, {date}, media_type={item.media_type}")
        if caption_excerpt:
            lines.append(f"  Caption: {caption_excerpt}")
        if ai_excerpt:
            lines.append(f"  AI caption/transcript: {ai_excerpt}")
        lines.append(f"  Tags: {tag_names}")

    return "\n".join(lines)


def build_messages(
    history: list[dict[str, str]],
    items: dict[int, Item],
    results: list[RetrievalResult],
    user_message: str,
) -> list[dict[str, str]]:
    """Build the full `messages` list for `ollama_client.stream_chat()`:
    system instruction, prior turns (`history`, oldest first, each a
    `{"role", "content"}` dict), then a final user turn that bundles the
    retrieved-item context with the new user message.

    Context is attached to the *latest* user turn (rather than as a
    separate system message) so it stays naturally scoped to "what's
    relevant to answer this question," and doesn't grow stale/duplicated
    across a long conversation.
    """
    context_block = build_context_block(items, results)
    final_user_content = f"{context_block}\n\nUser question: {user_message}"

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    messages.extend(history)
    messages.append({"role": "user", "content": final_user_content})
    return messages
