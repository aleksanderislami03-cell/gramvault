"""AI pipeline package (Ollama client + ChromaDB embedding store).

Owned by Agent A3 (AI pipeline). The `ollama_client` and `embedding_store`
modules in this package did not exist yet when Agent A4 (chat/search)
started its work, so A4 created minimal scaffolds for both — see the
"SCAFFOLD" notice at the top of each file. A3: please review and replace
with your real implementation, keeping the public function signatures
stable (A4's `gramvault.chat.*` package imports them) or coordinate any
signature changes with A4.
"""

from __future__ import annotations
