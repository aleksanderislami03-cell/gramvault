/**
 * Typed fetch wrapper for the GramVault backend API.
 *
 * Base URL resolution:
 *  - In production, `gramvault.main` serves the built frontend and the API
 *    from the same origin, so a relative `/api/...` path works with no
 *    configuration.
 *  - In dev, Vite's dev server proxies `/api` to the FastAPI dev server
 *    (see `vite.config.ts`), so relative paths work there too.
 *  - `VITE_API_BASE_URL` can override this (e.g. to point at a backend
 *    running on a non-default host/port without touching the proxy).
 */

import type { ChatCitation } from '../types'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown, message?: string) {
    super(message ?? `API error ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

/** True while a stub route (see backend/gramvault/api/routes_*.py) still
 * raises HTTP 501 "Not implemented". Callers use this to render a
 * friendly "not built yet" state instead of a generic error. */
export function isNotImplemented(err: unknown): boolean {
  return err instanceof ApiError && err.status === 501
}

export function isNotFound(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404
}

function buildUrl(path: string, params?: Record<string, unknown>): string {
  const url = new URL(`${API_BASE_URL}${path}`, window.location.origin)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue
      url.searchParams.set(key, String(value))
    }
  }
  return url.pathname + url.search
}

async function parseResponse<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T
  const text = await res.text()
  const body = text ? JSON.parse(text) : undefined
  if (!res.ok) {
    const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : body
    throw new ApiError(res.status, detail, typeof detail === 'string' ? detail : undefined)
  }
  return body as T
}

interface RequestOptions {
  method?: string
  params?: Record<string, unknown>
  body?: unknown
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', params, body, signal } = options
  const res = await fetch(buildUrl(path, params), {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  })
  return parseResponse<T>(res)
}

export const api = {
  get: <T>(path: string, params?: Record<string, unknown>, signal?: AbortSignal) =>
    request<T>(path, { method: 'GET', params, signal }),
  post: <T>(path: string, body?: unknown, params?: Record<string, unknown>) =>
    request<T>(path, { method: 'POST', body, params }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

/** Upload a file via multipart/form-data (fetch handles the boundary when
 * we pass a FormData body directly, so this bypasses the JSON `request`
 * helper above). */
export async function uploadFile<T>(
  path: string,
  file: File,
  fieldName = 'file',
  onProgress?: (pct: number) => void,
): Promise<T> {
  // Use XHR instead of fetch so we can report upload progress.
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', buildUrl(path))
    xhr.upload.onprogress = (evt) => {
      if (onProgress && evt.lengthComputable) {
        onProgress(Math.round((evt.loaded / evt.total) * 100))
      }
    }
    xhr.onload = () => {
      const text = xhr.responseText
      const body = text ? JSON.parse(text) : undefined
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as T)
      } else {
        const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : body
        reject(new ApiError(xhr.status, detail, typeof detail === 'string' ? detail : undefined))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, null, 'Network error'))
    const form = new FormData()
    form.append(fieldName, file)
    xhr.send(form)
  })
}

/**
 * Resolve a `MediaFile.file_path` (POSIX path relative to the configured
 * library dir, e.g. "media/ab/ab34...ef.jpg" — see
 * `backend/gramvault/ingestion/organizer.py`) into a URL an `<img>`/`<video>`
 * tag can load.
 *
 * GAP: as of this writing, `backend/gramvault/main.py` does not mount any
 * static route over `config.resolved_library_dir` — it only mounts
 * `frontend/dist` at `/`. This assumes a future `/media` static mount
 * (e.g. `app.mount("/media", StaticFiles(directory=str(config.resolved_library_dir)), name="media")`)
 * is added on the backend. Until that exists, URLs built here will 404.
 */
export function mediaUrl(filePath: string): string {
  const encoded = filePath.split('/').map(encodeURIComponent).join('/')
  return `${API_BASE_URL}/media/${encoded}`
}

export interface ChatStreamDonePayload {
  message_id: number
  content: string
  citations: ChatCitation[]
}

export interface ChatStreamHandlers {
  onToken?: (content: string) => void
  onDone?: (payload: ChatStreamDonePayload) => void
  onError?: (detail: string) => void
}

/**
 * POST a new chat message and consume the Server-Sent Events response from
 * `POST /api/chat/sessions/:id/messages` (see
 * `backend/gramvault/chat/service.py::stream_message` for the exact wire
 * contract: `event: token|done|error` frames with JSON `data`).
 *
 * The browser's native `EventSource` only supports GET requests, so this
 * sends the POST via `fetch` and parses the `text/event-stream` framing
 * from the response body manually.
 */
export async function streamChatMessage(
  sessionId: number,
  content: string,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(buildUrl(`/api/chat/sessions/${sessionId}/messages`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
      signal,
    })
  } catch {
    handlers.onError?.('Network error while sending message')
    return
  }

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '')
    let detail: unknown = text
    try {
      const parsed = text ? JSON.parse(text) : undefined
      detail = parsed && typeof parsed === 'object' && 'detail' in parsed ? parsed.detail : text
    } catch {
      // not JSON, fall back to raw text
    }
    handlers.onError?.(typeof detail === 'string' && detail ? detail : `Request failed (${res.status})`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const handleFrame = (rawFrame: string) => {
    let eventName = 'message'
    const dataLines: string[] = []
    for (const line of rawFrame.split('\n')) {
      if (!line || line.startsWith(':')) continue // blank/comment (keep-alive)
      if (line.startsWith('event:')) eventName = line.slice('event:'.length).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice('data:'.length).trim())
    }
    if (dataLines.length === 0) return
    const data = dataLines.join('\n')
    if (eventName === 'token') {
      const parsed = JSON.parse(data) as { content: string }
      handlers.onToken?.(parsed.content)
    } else if (eventName === 'done') {
      handlers.onDone?.(JSON.parse(data) as ChatStreamDonePayload)
    } else if (eventName === 'error') {
      const parsed = JSON.parse(data) as { detail: string }
      handlers.onError?.(parsed.detail)
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let sepIndex = buffer.indexOf('\n\n')
    while (sepIndex !== -1) {
      handleFrame(buffer.slice(0, sepIndex))
      buffer = buffer.slice(sepIndex + 2)
      sepIndex = buffer.indexOf('\n\n')
    }
  }
  if (buffer.trim()) handleFrame(buffer)
}

export { API_BASE_URL }
