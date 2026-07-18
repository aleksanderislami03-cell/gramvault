# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅        |
| < 0.1   | ❌        |

GramVault is a young project — fixes land on `main` and ship in the next
release rather than being backported.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Instead, use
GitHub's private vulnerability reporting:

**[Report a vulnerability](https://github.com/aleksanderislami03-cell/gramvault/security/advisories/new)**
(repo → Security tab → "Report a vulnerability")

You'll get a response as soon as possible (this is a spare-time project —
please allow up to two weeks). Once a fix is out, the advisory is published
and you'll be credited unless you'd rather not be.

## Scope notes

GramVault is local-first by design: it binds to `127.0.0.1`, makes no
network calls except to a localhost Ollama, and has no accounts, telemetry,
or hosted components. The most security-relevant surfaces are:

- **ZIP import** (`backend/gramvault/ingestion/`) — parsing untrusted
  archive contents (path traversal / zip-slip, decompression issues).
- **Obsidian export** (`backend/gramvault/export/`) — writing files to a
  user-supplied path.
- **Markdown/HTML rendering** in the frontend — content from an export is
  attacker-influenced if someone imports a malicious ZIP.

Reports in those areas are especially welcome. "The server is reachable by
other apps on the same machine" is inherent to a localhost web app and not
considered a vulnerability by itself, but bypasses of the localhost-only
binding are in scope.
