"""RAG chat + semantic search package (Agent A4).

Modules:
    retrieval.py  — hybrid (vector + keyword) retrieval over the library.
    prompt.py     — builds the RAG prompt (system instruction + numbered,
                    citable retrieved-item context) fed to the chat model.
    service.py    — orchestration: persistence, streaming chat completion,
                    citation-marker parsing.

See `gramvault.api.routes_chat` for the HTTP surface built on top of this.
"""

from __future__ import annotations
