"""FastAPI routers for GramVault.

Each `routes_*.py` module owns one feature area and is a STUB: routes,
HTTP methods, and request/response Pydantic models are fully defined, but
handler bodies raise HTTP 501 with a pointer to the agent responsible for
the real implementation. All routers are wired into the app in `main.py`.

  routes_import.py   -> Agent A2 (ingestion): ZIP upload, import job tracking
  routes_library.py  -> Agent A2/A5: gallery grid, filters, tags
  routes_enrich.py   -> Agent A3 (AI pipeline): enrichment trigger + progress
  routes_chat.py      -> Agent A4 (chat/search): RAG chat (SSE) + semantic search
  routes_export.py   -> Agent A6 (Obsidian exporter): export trigger + status
"""
