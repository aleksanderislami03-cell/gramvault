# GramVault Roadmap

GramVault v1 is feature-complete: ingestion, the AI enrichment pipeline, chat/search (RAG), the frontend, and Obsidian export are all implemented and tested (see [README.md](README.md) for the full feature list).

This document tracks nice-to-have ideas and follow-up work for future contributors, grouped by area. None of these are required for v1 — they're possibilities, not commitments. If you want to pick one up, see [CONTRIBUTING.md](CONTRIBUTING.md) for how to get set up, and consider opening an issue first to discuss the approach.

## Ingestion

*(No outstanding items logged during v1 development. Ideas: support for incremental/delta re-imports of a newer export without re-processing unchanged items; import progress reporting over the existing SSE infrastructure used by chat.)*

## AI pipeline

*(No outstanding items logged during v1 development. Ideas: pluggable embedding/vision models beyond the Ollama defaults; GPU-aware batching for faster-whisper transcription; configurable enrichment concurrency.)*

## Chat / search

*(No outstanding items logged during v1 development. Ideas: conversation history persistence across sessions; multi-turn follow-up citation tracking; adjustable retrieval parameters (top-k, hybrid weighting) exposed in Settings.)*

## Frontend

*(No outstanding items logged during v1 development. Ideas: gallery virtualization for very large libraries; saved search/filter presets; a dedicated "link-only" item badge distinguishing items with metadata but no local media.)*

## Obsidian exporter

*(No outstanding items logged during v1 development. Ideas: selective re-export by tag/date range from the Gallery UI rather than whole-library only; configurable note templates.)*

## Release engineering

- Real screenshots for the README (see the `<!-- TODO: screenshot -->` placeholders) once there's a populated library to capture.
- Optional: a signed/packaged release (e.g. a PyInstaller or Electron-wrapped build) for users who don't want to set up a Python/Node dev environment.
