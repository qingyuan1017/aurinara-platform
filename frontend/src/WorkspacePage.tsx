import { useCallback, useEffect, useState } from 'react'
import { Cloud, ExternalLink, HardDrive, LoaderCircle, SquareTerminal, StopCircle } from 'lucide-react'
import { WorkspaceApiError, workspaceApi, type WorkspaceState } from './workspace-client'

const sleep = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds))

function formatBytes(value: number): string {
  if (!value) return '0 MB'
  return `${(value / 1024 ** 3).toFixed(value >= 1024 ** 3 ? 1 : 2)} GB`
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Workspace request failed'
}

export function WorkspacePage() {
  const [workspace, setWorkspace] = useState<WorkspaceState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [readyUrl, setReadyUrl] = useState('')
  const [nowMilliseconds, setNowMilliseconds] = useState(() => Date.now())

  const refresh = useCallback(async () => {
    const current = await workspaceApi.current()
    setWorkspace(current)
    return current
  }, [])

  useEffect(() => { void refresh().catch((reason) => setError(errorMessage(reason))) }, [refresh])
  useEffect(() => {
    const timer = window.setInterval(() => setNowMilliseconds(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const remaining = workspace?.session_expires_at
    ? (() => {
        const seconds = Math.max(0, Math.floor((Date.parse(workspace.session_expires_at) - nowMilliseconds) / 1000))
        return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
      })()
    : null

  const open = async () => {
    const popup = window.open('about:blank', '_blank')
    if (popup) popup.document.title = 'Preparing Aurinara Workspace…'
    setBusy(true); setError(''); setReadyUrl('')
    try {
      let current = await workspaceApi.start()
      setWorkspace(current)
      while (current.status === 'pending') {
        await sleep(2000)
        current = await refresh()
      }
      if (current.status !== 'running') throw new Error('Workspace did not become ready')
      const launch = await workspaceApi.launch()
      if (popup) popup.location.replace(launch.url)
      else setReadyUrl(launch.url)
    } catch (reason) {
      popup?.close()
      setError(errorMessage(reason))
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    setBusy(true); setError('')
    try {
      setWorkspace(await workspaceApi.stop())
      let current = await refresh()
      while (current.status === 'stopping') {
        await sleep(2000)
        current = await refresh()
      }
    } catch (reason) {
      const suffix = reason instanceof WorkspaceApiError && reason.code === 'workspace_quota_exceeded'
        ? ' Delete or move files until usage is below 5 GB, then try again.' : ''
      setError(`${errorMessage(reason)}${suffix}`)
    } finally {
      setBusy(false)
    }
  }

  if (!workspace) return <div className="workspace-card workspace-loading"><LoaderCircle className="spin" />Loading workspace…</div>
  const active = ['pending', 'running', 'stopping'].includes(workspace.status)
  const syncLabel = workspace.sync_state === 'quota_exceeded' ? 'Storage limit exceeded'
    : workspace.sync_state === 'synced' ? 'Saved to cloud'
      : workspace.sync_state === 'syncing' ? 'Saving…' : workspace.sync_state

  return <section className="workspace-page">
    <div className="workspace-hero">
      <div className="workspace-icon"><SquareTerminal /></div>
      <div><span className="eyebrow">Personal development environment</span><h2>Your {workspace.product} workspace</h2><p>Python, R, Node.js, Git, and VS Code in the browser. Files persist automatically between sessions.</p></div>
    </div>
    <div className="workspace-card">
      <div className="workspace-summary">
        <div><span>Status</span><strong className={`workspace-state ${workspace.status}`}>{workspace.status.replace('_', ' ')}</strong></div>
        <div><span><HardDrive />Storage</span><strong>{formatBytes(workspace.usage_bytes)} / {formatBytes(workspace.quota_bytes)}</strong></div>
        <div><span><Cloud />Persistence</span><strong className={workspace.sync_state === 'quota_exceeded' ? 'workspace-warning' : ''}>{syncLabel}</strong></div>
        <div><span>Session remaining</span><strong>{active && remaining ? remaining : 'Not running'}</strong></div>
      </div>
      {workspace.sync_state === 'quota_exceeded' && <p className="workspace-alert">Your workspace is above 5 GB. Reduce its size before stopping so the latest files can be saved.</p>}
      {error && <p className="form-error">{error}</p>}
      {readyUrl && <p className="inline-note">Your browser blocked the new tab. <a href={readyUrl} target="_blank" rel="noreferrer">Workspace ready — Open <ExternalLink /></a></p>}
      <div className="workspace-actions">
        <button className="primary" disabled={busy || !workspace.can_open} onClick={() => void open()}>{busy ? <LoaderCircle className="spin" /> : <SquareTerminal />}Open Workspace</button>
        {active && <button className="ghost" disabled={busy || !workspace.can_stop} onClick={() => void stop()}><StopCircle />Stop Workspace</button>}
      </div>
      <small>Sessions stop automatically after two hours. Changes are synchronized every minute.</small>
    </div>
  </section>
}
