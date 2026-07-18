/**
 * TypeScript mirrors of the shared Pydantic models in
 * `backend/gramvault/models/schemas.py`. Keep these in sync by hand —
 * there is no codegen step (yet). Field names/shapes match the JSON the
 * FastAPI backend serializes (pydantic's default `model_dump(mode="json")`
 * behavior: datetimes as ISO 8601 strings, enums as their string values).
 */

export type MediaType = 'photo' | 'video' | 'reel' | 'carousel'

/** Media type for an individual file on disk (a carousel Item is made of
 * several photo/video MediaFile rows). */
export type FileMediaType = 'photo' | 'video'

/** Shared status enum for long-running jobs (import, export). */
export type JobStatus = 'pending' | 'running' | 'done' | 'failed'

export type EnrichmentStatus = 'pending' | 'running' | 'done' | 'failed'

export type TagKind = 'auto' | 'manual' | 'hashtag'

export type ChatRole = 'user' | 'assistant' | 'system'

export interface Author {
  id: number | null
  username: string
  full_name: string | null
  profile_url: string | null
  avatar_path: string | null
  created_at: string | null
}

export interface Tag {
  id: number | null
  name: string
  kind: TagKind
}

export interface MediaFile {
  id: number | null
  item_id: number
  file_path: string
  media_type: FileMediaType
  sequence_index: number
  width: number | null
  height: number | null
  duration_seconds: number | null
  /** Populated by faster-whisper for videos/reels (Agent A3). */
  transcript: string | null
  /** Populated by the llava vision model (Agent A3). */
  vision_caption: string | null
  checksum: string | null
}

export interface Item {
  id: number | null
  external_id: string | null
  author: Author | null
  media_type: MediaType
  caption: string | null
  permalink: string | null
  taken_at: string | null
  imported_at: string | null
  import_job_id: number | null
  enrichment_status: EnrichmentStatus
  tags: Tag[]
  media_files: MediaFile[]
}

export interface ImportJob {
  id: number | null
  source_path: string
  status: JobStatus
  total_items: number
  processed_items: number
  failed_items: number
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

export interface ChatCitation {
  id: number | null
  message_id: number | null
  item_id: number
  media_file_id: number | null
  snippet: string | null
}

export interface ChatMessage {
  id: number | null
  session_id: number
  role: ChatRole
  content: string
  created_at: string | null
  citations: ChatCitation[]
}

export interface ChatSession {
  id: number | null
  title: string | null
  created_at: string | null
  updated_at: string | null
  messages: ChatMessage[]
}

// --- response/request wrapper shapes (from api/routes_*.py) ---

export interface ItemListResponse {
  items: Item[]
  total: number
  page: number
  page_size: number
}

export interface TagUpdateRequest {
  tags: string[]
}

export interface ImportJobListResponse {
  jobs: ImportJob[]
}

export interface EnrichmentRunRequest {
  item_ids: number[] | null
}

export interface EnrichmentRunResponse {
  queued_count: number
}

export interface EnrichmentProgress {
  total: number
  pending: number
  running: number
  done: number
  failed: number
}

export interface ChatSessionCreateRequest {
  title: string | null
}

export interface ChatMessageCreateRequest {
  content: string
}

export interface SemanticSearchResult {
  item: Item
  score: number
  snippet: string | null
}

export interface SemanticSearchResponse {
  query: string
  results: SemanticSearchResult[]
}

export interface ExportRequest {
  item_ids: number[] | null
  vault_subfolder: string | null
}

export interface ExportSkippedItem {
  item_id: number | null
  reason: string
}

export interface ExportJobStatus {
  id: number
  status: JobStatus
  vault_subfolder: string | null
  total_items: number
  processed_items: number
  failed_items: number
  notes_written: number
  notes_updated: number
  media_files_copied: number
  skipped: ExportSkippedItem[]
  error_message: string | null
}

export interface VaultPathCheckRequest {
  /** Omit (null) to validate the vault path already configured in config.yaml. */
  vault_dir: string | null
}

export interface VaultPathCheckResponse {
  valid: boolean
  reason: string | null
}

// --- library filter query params (GET /api/library/items) ---

export interface LibraryItemFilters {
  author?: string
  media_type?: MediaType
  tag?: string
  q?: string
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}
