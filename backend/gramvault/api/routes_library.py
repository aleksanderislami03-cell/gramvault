"""Library/gallery API — STUB for Agents A2/A5.

Owns: the browsable gallery grid, filtering by author/media type/tag/date/
free-text, single-item detail, and manual tag editing. Agent A2 defines
the actual query logic (it owns the underlying rows); Agent A5 consumes
this from the React frontend.

Every handler below is fully signed but raises HTTP 501.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from gramvault.api.deps import get_config_dependency
from gramvault.config import Config
from gramvault.models.schemas import Author, Item, MediaType, Tag

router = APIRouter(prefix="/api/library", tags=["library"])


class ItemListResponse(BaseModel):
    items: list[Item]
    total: int
    page: int
    page_size: int


class TagUpdateRequest(BaseModel):
    tags: list[str]


@router.get("/items", response_model=ItemListResponse)
async def list_items(
    author: str | None = Query(default=None, description="Filter by author username"),
    media_type: MediaType | None = Query(default=None, description="Filter by media type"),
    tag: str | None = Query(default=None, description="Filter by tag name"),
    q: str | None = Query(default=None, description="Free-text search over captions"),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    config: Config = Depends(get_config_dependency),
) -> ItemListResponse:
    """Paginated, filterable gallery listing.

    TODO(A2)/TODO(A5): implement the SQL query (joins across items,
    authors, media_files, tags/item_tags) with the filters above, and
    return a page of fully-populated `Item` objects (including their
    `media_files` and `tags`). Keep filter param names stable — the
    frontend (A5) wires its filter UI directly to these query params.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A2/A5 (library)")


@router.get("/items/{item_id}", response_model=Item)
async def get_item(
    item_id: int,
    config: Config = Depends(get_config_dependency),
) -> Item:
    """Fetch a single item with its media files and tags.

    TODO(A2): return 404 (not 501) once implemented if `item_id` is missing.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A2 (library)")


@router.get("/authors", response_model=list[Author])
async def list_authors(
    config: Config = Depends(get_config_dependency),
) -> list[Author]:
    """List all known authors (for filter dropdowns).

    TODO(A2): straightforward `SELECT * FROM authors ORDER BY username`.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A2 (library)")


@router.get("/tags", response_model=list[Tag])
async def list_tags(
    config: Config = Depends(get_config_dependency),
) -> list[Tag]:
    """List all known tags (for filter dropdowns / tag-cloud UI).

    TODO(A2)/TODO(A3): auto-generated tags come from the AI pipeline (A3);
    this endpoint just needs to read the `tags` table.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A2 (library)")


@router.patch("/items/{item_id}/tags", response_model=Item)
async def update_item_tags(
    item_id: int,
    body: TagUpdateRequest,
    config: Config = Depends(get_config_dependency),
) -> Item:
    """Replace an item's manually-assigned tags.

    TODO(A2)/TODO(A5): upsert rows into `tags` (kind='manual' for new
    names) and rewrite `item_tags` for this item. Consider whether
    auto/hashtag tags should be preserved separately from manual edits —
    your call, just document the decision here once implemented.
    """
    raise HTTPException(status_code=501, detail="Not implemented — see Agent A2/A5 (library)")
