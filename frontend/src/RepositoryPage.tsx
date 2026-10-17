import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Archive, Clock3, Download, File, Folder, FolderInput, History, MoreHorizontal, Plus, RefreshCw, RotateCcw, Search, Upload } from 'lucide-react'
import { api, ApiError, uploadNeutralFile } from './api'
import type { RepositoryNode, RepositoryVersion, Study } from './types'

type Notify = (kind: 'ok' | 'error', text: string) => void
type View = 'folder' | 'all' | 'recent' | 'aurinara' | 'aurinara-edc' | 'platform' | 'archived'

const views: Array<{ id: View; label: string }> = [
  { id: 'folder', label: 'Files' }, { id: 'all', label: 'All' }, { id: 'recent', label: 'Recent' },
  { id: 'aurinara', label: 'Aurinara' }, { id: 'aurinara-edc', label: 'EDC' },
  { id: 'platform', label: 'Neutral' }, { id: 'archived', label: 'Archived' },
]

function message(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} · ${error.code}`
  return error instanceof Error ? error.message : 'Unexpected error'
}

export function RepositoryPage({ studies, notify }: { studies: Study[]; notify: Notify }) {
  const [studyId, setStudyId] = useState(studies.find((item) => item.status === 'active')?.id || studies[0]?.id || '')
  const [view, setView] = useState<View>('folder')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<RepositoryNode[]>([])
  const [folders, setFolders] = useState<RepositoryNode[]>([])
  const [path, setPath] = useState<RepositoryNode[]>([])
  const [selected, setSelected] = useState<RepositoryNode | null>(null)
  const [versions, setVersions] = useState<RepositoryVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const uploadRef = useRef<HTMLInputElement>(null)
  const currentFolder = path.at(-1) || null

  const load = useCallback(async () => {
    if (!studyId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ view, limit: '100' })
      if (view === 'folder' && currentFolder) params.set('parent_id', currentFolder.id)
      if (query.trim()) params.set('q', query.trim())
      const [page, folderPage] = await Promise.all([
        api.repository(studyId, params),
        api.repository(studyId, new URLSearchParams({ view: 'all', limit: '100' })),
      ])
      setItems(page.items)
      setFolders(folderPage.items.filter((item) => item.kind === 'folder' && item.lifecycle_status === 'active'))
      setSelected((value) => page.items.find((item) => item.id === value?.id) || null)
    } catch (error) { notify('error', message(error)) }
    finally { setLoading(false) }
  }, [studyId, view, currentFolder, query, notify])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!selected || selected.kind !== 'file') { setVersions([]); return }
    void api.repositoryVersions(selected.id).then(setVersions).catch((error) => notify('error', message(error)))
  }, [selected, notify])

  const scopedDestinations = useMemo(() => folders.filter((folder) => selected &&
    folder.id !== selected.id && folder.source_product === selected.source_product &&
    folder.required_read_action === selected.required_read_action &&
    folder.required_write_action === selected.required_write_action && folder.site_id === selected.site_id), [folders, selected])

  const openFolder = (folder: RepositoryNode) => { setPath([...path, folder]); setView('folder'); setQuery(''); setSelected(null) }
  const createFolder = async () => {
    const name = prompt('Folder name')?.trim()
    if (!name || !studyId) return
    try { await api.createRepositoryFolder(studyId, { name, parent_id: currentFolder?.id || null }); notify('ok', `${name} created`); await load() }
    catch (error) { notify('error', message(error)) }
  }
  const rename = async () => {
    if (!selected) return
    const name = prompt('New name', selected.display_name)?.trim()
    if (!name || name === selected.display_name) return
    try { setSelected(await api.patchRepositoryNode(selected, { name })); notify('ok', 'Repository entry renamed'); await load() }
    catch (error) { notify('error', message(error)) }
  }
  const move = async (parentId: string) => {
    if (!selected) return
    try { setSelected(await api.patchRepositoryNode(selected, { parent_id: parentId || null })); notify('ok', 'Repository entry moved'); await load() }
    catch (error) { notify('error', message(error)) }
  }
  const archive = async () => {
    if (!selected) return
    try {
      const changed = selected.lifecycle_status === 'archived' ? await api.restoreRepositoryNode(selected) : await api.archiveRepositoryNode(selected)
      setSelected(changed); notify('ok', selected.lifecycle_status === 'archived' ? 'Entry restored' : 'Entry archived'); await load()
    } catch (error) { notify('error', message(error)) }
  }
  const download = async (version?: RepositoryVersion) => {
    const target = version || versions[0]
    if (!target) return
    try { const result = await api.downloadRepositoryVersion(target.id); window.open(result.url, '_blank', 'noopener,noreferrer') }
    catch (error) { notify('error', message(error)) }
  }
  const upload = async (event: ChangeEvent<HTMLInputElement>, replace?: RepositoryNode) => {
    const files = [...(event.target.files || [])]
    if (!studyId || files.length === 0) return
    setUploading(true)
    try {
      for (const file of files) await uploadNeutralFile(studyId, file, currentFolder?.id || null, replace)
      notify('ok', replace ? 'New version added' : `${files.length} file${files.length === 1 ? '' : 's'} uploaded`)
      await load()
      if (replace) setVersions(await api.repositoryVersions(replace.id))
    } catch (error) { notify('error', message(error)) }
    finally { setUploading(false); event.target.value = '' }
  }

  if (!studyId) return <div className="empty"><Folder /><span>Create or join a study to use Repository.</span></div>
  return <div className="repository-shell">
    <section className="repository-commandbar">
      <select aria-label="Study" value={studyId} onChange={(event) => { setStudyId(event.target.value); setPath([]); setSelected(null) }}>{studies.map((study) => <option key={study.id} value={study.id}>{study.code} · {study.display_name}</option>)}</select>
      <div className="search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search authorized files" /></div>
      <button className="secondary" onClick={() => void createFolder()}><Plus />Folder</button>
      <button className="primary" disabled={uploading} onClick={() => uploadRef.current?.click()}><Upload />{uploading ? 'Uploading…' : 'Upload'}</button>
      <input ref={uploadRef} hidden multiple type="file" onChange={(event) => void upload(event)} />
      <button className="icon secondary" aria-label="Refresh" onClick={() => void load()}><RefreshCw className={loading ? 'spinner' : ''} /></button>
    </section>
    <nav className="repository-views" aria-label="Repository views">{views.map((item) => <button key={item.id} className={view === item.id ? 'active' : ''} onClick={() => { setView(item.id); setSelected(null) }}>{item.label}</button>)}</nav>
    <div className="repository-grid">
      <aside className="folder-tree"><button className={!currentFolder ? 'active' : ''} onClick={() => { setPath([]); setView('folder') }}><Folder />Repository</button>{folders.filter((folder) => folder.parent_id === null).map((folder) => <button key={folder.id} className={currentFolder?.id === folder.id ? 'active' : ''} onClick={() => { setPath([folder]); setView('folder') }}><Folder />{folder.display_name}</button>)}</aside>
      <section className="repository-list">
        <div className="breadcrumbs"><button onClick={() => { setPath([]); setView('folder') }}>Repository</button>{path.map((folder, index) => <span key={folder.id}>/ <button onClick={() => setPath(path.slice(0, index + 1))}>{folder.display_name}</button></span>)}<small>{items.length} visible</small></div>
        <div className="repo-table-head"><span>Name</span><span>Source</span><span>Status</span><span>Updated</span></div>
        {items.map((item) => <button key={item.id} className={`repo-row ${selected?.id === item.id ? 'selected' : ''}`} onClick={() => setSelected(item)} onDoubleClick={() => item.kind === 'folder' ? openFolder(item) : void download()}>
          <span className="repo-name">{item.kind === 'folder' ? <Folder /> : <File />}<span><strong>{item.display_name}</strong><small>{item.kind === 'file' ? `${item.current_version_number || 0} version${item.current_version_number === 1 ? '' : 's'}` : 'Folder'}</small></span></span>
          <span>{item.source_product === 'platform' ? 'Neutral' : item.source_product}</span><span className="status"><i />{item.lifecycle_status === 'archived' ? 'archived' : item.source_status}</span><span>{new Date(item.updated_at).toLocaleDateString()}</span>
        </button>)}
        {!loading && items.length === 0 && <div className="empty"><Folder /><span>No authorized entries in this view.</span></div>}
      </section>
      <aside className="repository-detail">{selected ? <>
        <header><span className="repo-detail-icon">{selected.kind === 'folder' ? <Folder /> : <File />}</span><div><h2>{selected.display_name}</h2><p>{selected.source_product} · {selected.classification}</p></div><MoreHorizontal /></header>
        <div className="repo-actions"><button className="secondary" onClick={() => void rename()}>Rename</button>{selected.kind === 'file' && <button className="primary" onClick={() => void download()}><Download />Download</button>}<button className="secondary" onClick={() => void archive()}>{selected.lifecycle_status === 'archived' ? <><RotateCcw />Restore</> : <><Archive />Archive</>}</button></div>
        {selected.lifecycle_status === 'active' && <label className="field"><span>Move to equivalent-scope folder</span><select value={selected.parent_id || ''} onChange={(event) => void move(event.target.value)}><option value="">Repository root</option>{scopedDestinations.map((folder) => <option key={folder.id} value={folder.id}>{folder.display_name}</option>)}</select></label>}
        <dl><dt>Type</dt><dd>{selected.source_resource_type}</dd><dt>Module</dt><dd>{selected.module || '—'}</dd><dt>Scope</dt><dd>{selected.site_id ? `Site ${selected.site_id}` : 'Study'}</dd><dt>Read permission</dt><dd className="mono">{selected.required_read_action}</dd><dt>Resource ID</dt><dd className="mono">{selected.source_resource_id || '—'}</dd></dl>
        {selected.kind === 'file' && <section className="version-list"><h3><History />Version history</h3>{selected.source_product === 'platform' && selected.lifecycle_status === 'active' && <label className="replace-button"><Upload />Replace<input hidden type="file" onChange={(event) => void upload(event, selected)} /></label>}{versions.map((version) => <button key={version.id} onClick={() => void download(version)}><span>v{version.version_number}</span><small>{new Date(version.created_at).toLocaleString()} · {(version.size_bytes / 1024).toFixed(1)} KB</small><Download /></button>)}</section>}
      </> : <div className="empty"><FolderInput /><span>Select an entry to see details and versions.</span></div>}</aside>
    </div>
  </div>
}
