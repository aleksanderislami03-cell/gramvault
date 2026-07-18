"""Ingestion package (Agent A2): parses Instagram "Download Your Information"
export ZIPs, organizes any embedded media files by content hash, and writes
authors/items/media_files rows tracked by an import_jobs row.

Public surface (stable — used by both `gramvault.cli` and
`gramvault.api.routes_import`):
    - `gramvault.ingestion.parser.parse_export` / `ExportFormatError`
    - `gramvault.ingestion.organizer.organize_zip_member`
    - `gramvault.ingestion.importer.import_zip` (the one-call convenience
      wrapper both the CLI and the API route use)
"""

from __future__ import annotations

from gramvault.ingestion.importer import import_zip
from gramvault.ingestion.parser import ExportFormatError, ParsedExport, parse_export

__all__ = [
    "ExportFormatError",
    "ParsedExport",
    "import_zip",
    "parse_export",
]
