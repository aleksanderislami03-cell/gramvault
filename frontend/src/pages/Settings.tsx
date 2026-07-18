import { useState } from 'react'
import { api } from '../api/client'
import type { ExportJobStatus, ExportRequest, VaultPathCheckRequest, VaultPathCheckResponse } from '../types'

export function Settings() {
  const [vaultPath, setVaultPath] = useState('')
  const [subfolder, setSubfolder] = useState('')
  const [validation, setValidation] = useState<VaultPathCheckResponse | null>(null)
  const [validating, setValidating] = useState(false)

  const [job, setJob] = useState<ExportJobStatus | null>(null)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  async function validate() {
    setValidating(true)
    setValidation(null)
    try {
      const body: VaultPathCheckRequest = { vault_dir: vaultPath.trim() || null }
      const res = await api.post<VaultPathCheckResponse>('/api/export/validate-vault', body)
      setValidation(res)
    } catch (err) {
      setValidation({ valid: false, reason: err instanceof Error ? err.message : 'Validation failed' })
    } finally {
      setValidating(false)
    }
  }

  // The export endpoint always reads paths.obsidian_vault_dir from
  // config.yaml — it never accepts a vault path in its own request body.
  // So the typed path has to be persisted here first, or "Export" would
  // silently export to whatever vault (if any) is already on disk.
  async function saveVaultPath(): Promise<boolean> {
    setSaving(true)
    setSaved(false)
    try {
      const body: VaultPathCheckRequest = { vault_dir: vaultPath.trim() || null }
      const res = await api.post<VaultPathCheckResponse>('/api/export/vault-path', body)
      setValidation(res)
      setSaved(res.valid)
      return res.valid
    } catch (err) {
      setValidation({ valid: false, reason: err instanceof Error ? err.message : 'Save failed' })
      return false
    } finally {
      setSaving(false)
    }
  }

  async function pollJob(jobId: number) {
    for (let i = 0; i < 600; i++) {
      const status = await api.get<ExportJobStatus>(`/api/export/jobs/${jobId}`)
      setJob(status)
      if (status.status === 'done' || status.status === 'failed') break
      await new Promise((r) => setTimeout(r, 1500))
    }
  }

  async function runExport() {
    setError(null)
    setExporting(true)
    setJob(null)
    try {
      // Only overwrite the saved vault path if the user actually typed
      // something — leaving it blank means "use what's already configured".
      if (vaultPath.trim() && !(await saveVaultPath())) {
        setError('Vault path is invalid — fix it before exporting.')
        return
      }
      const body: ExportRequest = { item_ids: null, vault_subfolder: subfolder.trim() || null }
      const started = await api.post<ExportJobStatus>('/api/export/obsidian', body)
      setJob(started)
      await pollJob(started.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-8 px-4 py-6">
      <section className="flex flex-col gap-3">
        <h1 className="text-lg font-semibold text-slate-100">Obsidian export</h1>

        <label className="flex flex-col gap-1">
          <span className="label">Vault folder path</span>
          <span className="text-xs text-slate-500">
            Leave blank to validate/use the path already configured in <code>config.yaml</code> (
            <code>paths.obsidian_vault_dir</code>).
          </span>
          <input
            className="input"
            placeholder="C:\Users\you\ObsidianVault"
            value={vaultPath}
            onChange={(e) => setVaultPath(e.target.value)}
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="label">Subfolder within vault (optional)</span>
          <input
            className="input"
            placeholder="GramVault"
            value={subfolder}
            onChange={(e) => setSubfolder(e.target.value)}
          />
        </label>

        <div className="flex gap-2">
          <button type="button" className="btn-secondary" onClick={() => void validate()} disabled={validating}>
            {validating ? 'Validating…' : 'Validate path'}
          </button>
          <button type="button" className="btn-secondary" onClick={() => void saveVaultPath()} disabled={saving}>
            {saving ? 'Saving…' : 'Save vault path'}
          </button>
          <button type="button" className="btn-primary" onClick={() => void runExport()} disabled={exporting}>
            {exporting ? 'Exporting…' : 'Export to Obsidian'}
          </button>
        </div>

        {validation && (
          <p className={`text-sm ${validation.valid ? 'text-emerald-300' : 'text-red-300'}`}>
            {validation.valid ? (saved ? 'Saved.' : 'Valid vault path.') : validation.reason}
          </p>
        )}

        {error && <p className="card border-red-900/60 bg-red-950/40 px-4 py-2 text-sm text-red-300">{error}</p>}

        {job && (
          <div className="card flex flex-col gap-1 px-4 py-3 text-sm text-slate-300">
            <span>
              Export {job.status}: {job.processed_items}/{job.total_items} items
            </span>
            <span className="text-xs text-slate-500">
              {job.notes_written} notes written, {job.notes_updated} updated, {job.media_files_copied} media files copied
            </span>
            {job.failed_items > 0 && <span className="text-amber-300">{job.failed_items} items skipped</span>}
            {job.skipped.length > 0 && (
              <ul className="list-disc pl-5 text-xs text-slate-500">
                {job.skipped.map((s, i) => (
                  <li key={i}>
                    {s.item_id !== null ? `Item #${s.item_id}` : 'Unknown item'}: {s.reason}
                  </li>
                ))}
              </ul>
            )}
            {job.error_message && <span className="text-red-300">{job.error_message}</span>}
          </div>
        )}
      </section>

      <ModelSettings />
    </div>
  )
}

function ModelSettings() {
  // GAP: there is no config-read API endpoint exposed by the backend (see
  // backend/gramvault/config.py / gramvault/api/routes_*.py) — model names
  // live only in config.yaml on disk and aren't served over HTTP anywhere.
  // Rather than invent an endpoint, this just documents where to look.
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-lg font-semibold text-slate-100">AI models</h2>
      <p className="card px-4 py-3 text-sm text-slate-400">
        Model configuration isn&apos;t exposed via the API yet. Chat/vision/embedding model names are set in{' '}
        <code>config.yaml</code> under <code>models.chat_model</code>, <code>models.vision_model</code>, and{' '}
        <code>models.embedding_model</code> — edit that file directly and restart the server to change them.
      </p>
    </section>
  )
}
