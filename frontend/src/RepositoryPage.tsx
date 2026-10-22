import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Archive, Clock3, Database, Download, Eye, File, FileSpreadsheet, FileText, Folder, FolderInput, History, MoreHorizontal, Plus, Presentation, RefreshCw, RotateCcw, Search, Table2, Upload, X } from 'lucide-react'
import { api, ApiError, uploadNeutralFile, waitForRepositoryJob, type UploadProgress } from './api'
import { RepositoryViewer } from './RepositoryViewer'
import type { RepositoryNode, RepositoryVersion, Site, Study } from './types'

type Notify = (kind: 'ok' | 'error', text: string) => void
type View = 'folder' | 'all' | 'recent' | 'aurinara' | 'aurinara-edc' | 'platform' | 'archived'

const views: Array<{ id: View; label: string }> = [
  { id: 'folder', label: 'Files' }, { id: 'all', label: 'All' }, { id: 'recent', label: 'Recent' },
  { id: 'aurinara', label: 'Aurinara' }, { id: 'aurinara-edc', label: 'EDC' },
  { id: 'platform', label: 'Neutral' }, { id: 'archived', label: 'Archived' },
]

const extensionKinds: Record<string, string> = {
  pdf: 'pdf', docx: 'docx', xlsx: 'xlsx', pptx: 'pptx', csv: 'csv', parquet: 'parquet',
}

function repositoryFileKind(node: RepositoryNode): string {
  if (node.kind === 'folder') return 'folder'
  const detected = node.current_version?.profile?.file_kind
  if (detected && detected !== 'unknown') return detected
  return extensionKinds[node.display_name.split('.').pop()?.toLocaleLowerCase() || ''] || 'file'
}

function RepositoryEntryIcon({ node }: { node: RepositoryNode }) {
  const kind = repositoryFileKind(node)
  const props = { className: `repository-file-icon ${kind}`, 'aria-label': kind === 'folder' ? 'Folder' : `${kind.toUpperCase()} file` }
  if (kind === 'folder') return <Folder {...props} />
  if (kind === 'pdf' || kind === 'docx') return <FileText {...props} />
  if (kind === 'xlsx') return <FileSpreadsheet {...props} />
  if (kind === 'pptx') return <Presentation {...props} />
  if (kind === 'csv') return <Table2 {...props} />
  if (kind === 'parquet') return <Database {...props} />
  return <File {...props} />
}

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
  const [sites, setSites] = useState<Site[]>([])
  const [path, setPath] = useState<RepositoryNode[]>([])
  const [selected, setSelected] = useState<RepositoryNode | null>(null)
  const [versions, setVersions] = useState<RepositoryVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null)
  const [collectionCapabilities, setCollectionCapabilities] = useState({ can_upload: false, can_create_folder: false })
  const [viewerOpen, setViewerOpen] = useState(false)
  const uploadRef = useRef<HTMLInputElement>(null)
  const uploadAbort = useRef<AbortController>()
  const deepNodeId = useRef<string | null>(null)
  const currentFolder = path.at(-1) || null

  useEffect(() => {
    const parameters = new URLSearchParams(location.search)
    const linkedStudy = parameters.get('study')
    const linkedVersion = parameters.get('version')
    if (linkedStudy && studies.some((study) => study.id === linkedStudy)) setStudyId(linkedStudy)
    if (!linkedVersion) return
    setView('all')
    void api.repositoryVersion(linkedVersion).then(async (binding) => {
      if (linkedStudy && binding.study_id !== linkedStudy) throw new Error('Deep link study and version do not match')
      deepNodeId.current = binding.node_id
      setSelected(await api.repositoryNode(binding.node_id))
      setViewerOpen(true)
    }).catch((error) => notify('error', message(error)))
  }, [notify, studies])

  const load = useCallback(async () => {
    if (!studyId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ view, limit: '100' })
      if (view === 'folder' && currentFolder) params.set('parent_id', currentFolder.id)
      if (query.trim()) params.set('q', query.trim())
      const [page, folderPage, studySites] = await Promise.all([
        api.repository(studyId, params),
        api.repository(studyId, new URLSearchParams({ view: 'all', limit: '100' })),
        api.sites(studyId),
      ])
      setItems(page.items)
      setCollectionCapabilities(page.capabilities)
      setFolders(folderPage.items.filter((item) => item.kind === 'folder' && item.lifecycle_status === 'active'))
      setSites(studySites)
      setSelected((value) => page.items.find((item) => item.id === value?.id) || (value?.id === deepNodeId.current ? value : null))
    } catch (error) { notify('error', message(error)) }
    finally { setLoading(false) }
  }, [studyId, view, currentFolder, query, notify])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!selected || selected.kind !== 'file') { setVersions([]); return }
    void api.repositoryVersions(selected.id).then(setVersions).catch((error) => notify('error', message(error)))
  }, [selected, notify])

  useEffect(() => {
    if (!viewerOpen) return
    const previousOverflow = document.body.style.overflow
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setViewerOpen(false) }
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [viewerOpen])

  const scopedDestinations = useMemo(() => folders.filter((folder) => selected &&
    folder.id !== selected.id && folder.source_product === selected.source_product &&
    folder.required_read_action === selected.required_read_action &&
    folder.required_write_action === selected.required_write_action && folder.site_id === selected.site_id), [folders, selected])
  const siteNames = useMemo(() => new Map(sites.map((site) => [site.id, site.name])), [sites])

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
      uploadAbort.current = new AbortController()
      for (const file of files) await uploadNeutralFile(studyId, file, currentFolder?.id || null, replace, setUploadProgress, uploadAbort.current.signal)
      notify('ok', replace ? 'New version added' : `${files.length} file${files.length === 1 ? '' : 's'} uploaded`)
      await load()
      if (replace) setVersions(await api.repositoryVersions(replace.id))
    } catch (error) { notify('error', message(error)) }
    finally { setUploading(false); setUploadProgress(null); uploadAbort.current = undefined; event.target.value = '' }
  }

  const convert = async (target: 'csv' | 'parquet') => {
    const version = versions[0]
    if (!selected || !version) return
    const stem = selected.display_name.replace(/\.[^.]+$/, '')
    const filename = prompt('Output filename', `${stem}.${target}`)?.trim()
    if (!filename) return
    try { const job = await api.convertRepositoryVersion(version.id, target, filename); notify('ok', 'Conversion queued'); await waitForRepositoryJob(job); notify('ok', 'Conversion completed'); await load() }
    catch (error) { notify('error', message(error)) }
  }

  if (!studyId) return <div className="empty"><Folder /><span>Create or join a study to use Repository.</span></div>
  return <div className="repository-shell">
    <section className="repository-commandbar">
      <select aria-label="Study" value={studyId} onChange={(event) => { setStudyId(event.target.value); setPath([]); setSelected(null) }}>{studies.map((study) => <option key={study.id} value={study.id}>{study.code} · {study.display_name}</option>)}</select>
      <div className="search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search authorized files" /></div>
      <button className="secondary" disabled={!collectionCapabilities.can_create_folder} onClick={() => void createFolder()}><Plus />Folder</button>
      <button className="primary" disabled={uploading || !collectionCapabilities.can_upload} onClick={() => uploadRef.current?.click()}><Upload />{uploading ? 'Uploading…' : 'Upload'}</button>
      <input ref={uploadRef} hidden multiple type="file" onChange={(event) => void upload(event)} />
      {uploadProgress && <span className="upload-progress">{uploadProgress.phase} {Math.round(uploadProgress.loaded / Math.max(1, uploadProgress.total) * 100)}% <button onClick={() => uploadAbort.current?.abort()}>Cancel</button></span>}
      <button className="icon secondary" aria-label="Refresh" onClick={() => void load()}><RefreshCw className={loading ? 'spinner' : ''} /></button>
    </section>
    <nav className="repository-views" aria-label="Repository views">{views.map((item) => <button key={item.id} className={view === item.id ? 'active' : ''} onClick={() => { setView(item.id); setSelected(null) }}>{item.label}</button>)}</nav>
    <div className="repository-grid">
      <aside className="folder-tree"><button className={!currentFolder ? 'active' : ''} onClick={() => { setPath([]); setView('folder') }}><Folder />Repository</button>{folders.filter((folder) => folder.parent_id === null).map((folder) => <button key={folder.id} className={currentFolder?.id === folder.id ? 'active' : ''} onClick={() => { setPath([folder]); setView('folder') }}><Folder />{folder.display_name}</button>)}</aside>
      <section className="repository-list">
        <div className="breadcrumbs"><button onClick={() => { setPath([]); setView('folder') }}>Repository</button>{path.map((folder, index) => <span key={folder.id}>/ <button onClick={() => setPath(path.slice(0, index + 1))}>{folder.display_name}</button></span>)}<small>{items.length} visible</small></div>
        <div className="repo-table-head"><span>Name</span><span>Source</span><span>Status</span><span>Updated</span></div>
        {items.map((item) => <button key={item.id} className={`repo-row ${selected?.id === item.id ? 'selected' : ''}`} onClick={() => setSelected(item)} onDoubleClick={() => item.kind === 'folder' ? openFolder(item) : void download()}>
          <span className="repo-name"><RepositoryEntryIcon node={item}/><span><strong>{item.display_name}</strong><small>{item.kind === 'file' ? `${item.current_version_number || 0} version${item.current_version_number === 1 ? '' : 's'}` : 'Folder'}</small></span></span>
          <span>{item.source_product === 'platform' ? 'Neutral' : item.source_product}</span><span className="status"><i />{item.lifecycle_status === 'archived' ? 'archived' : item.source_status}</span><span>{new Date(item.updated_at).toLocaleDateString()}</span>
        </button>)}
        {!loading && items.length === 0 && <div className="empty"><Folder /><span>No authorized entries in this view.</span></div>}
      </section>
      <aside className="repository-detail">{selected ? <>
        <header><span className="repo-detail-icon"><RepositoryEntryIcon node={selected}/></span><div><h2>{selected.display_name}</h2><p>{selected.source_product} · {selected.classification}</p></div><MoreHorizontal /></header>
        <div className="repo-actions">{selected.capabilities.can_manage && <button className="secondary" onClick={() => void rename()}>Rename</button>}{selected.kind === 'file' && <button className="primary" onClick={() => void download()}><Download />Download</button>}{selected.kind === 'file' && selected.capabilities.can_preview && <button className="secondary" onClick={() => setViewerOpen(true)}><Eye />Open preview</button>}{selected.capabilities.can_manage && <button className="secondary" onClick={() => void archive()}>{selected.lifecycle_status === 'archived' ? <><RotateCcw />Restore</> : <><Archive />Archive</>}</button>}</div>
        {selected.lifecycle_status === 'active' && selected.capabilities.can_manage && <label className="field"><span>Move to equivalent-scope folder</span><select value={selected.parent_id || ''} onChange={(event) => void move(event.target.value)}><option value="">Repository root</option>{scopedDestinations.map((folder) => <option key={folder.id} value={folder.id}>{folder.display_name}</option>)}</select></label>}
        <dl><dt>Type</dt><dd>{selected.source_resource_type.replaceAll('_', ' ')}</dd><dt>Module</dt><dd>{selected.module || '—'}</dd><dt>Scope</dt><dd>{selected.site_id ? siteNames.get(selected.site_id) || 'Unavailable site' : 'Study-wide'}</dd></dl>
        <details className="technical-details"><summary>Technical details</summary><dl><dt>Read permission</dt><dd className="mono">{selected.required_read_action}</dd><dt>Internal reference</dt><dd className="mono">{selected.source_resource_id || '—'}</dd></dl></details>
        {selected.kind === 'file' && <section className="version-list"><h3><History />Version history</h3>{selected.capabilities.can_manage && selected.lifecycle_status === 'active' && <label className="replace-button"><Upload />New version<input hidden type="file" onChange={(event) => void upload(event, selected)} /></label>}{versions[0]?.capabilities.conversion_targets.map((target) => <button key={target} onClick={() => void convert(target)}>Convert to {target.toUpperCase()}</button>)}{versions.map((version) => <button key={version.id} onClick={() => void download(version)}><span>v{version.version_number}</span><small>{version.profile?.file_kind || version.media_type} · {version.profile?.processing_status || 'pending'} · {new Date(version.created_at).toLocaleString()} · {(version.size_bytes / 1024).toFixed(1)} KB</small><Download /></button>)}</section>}
      </> : <div className="empty"><FolderInput /><span>Select an entry to see details and versions.</span></div>}</aside>
    </div>
    {viewerOpen && selected?.kind === 'file' && versions[0] && <div className="repository-preview-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setViewerOpen(false) }}>
      <section className="repository-preview-workspace" role="dialog" aria-modal="true" aria-label={`Preview ${selected.display_name}`}>
        <header className="repository-preview-header">
          <div className="repository-preview-title"><span className="repo-detail-icon"><RepositoryEntryIcon node={selected}/></span><div><small>{versions[0].profile?.file_kind?.toUpperCase() || 'FILE'} · Version {versions[0].version_number}</small><h2>{selected.display_name}</h2></div></div>
          <div className="repository-preview-actions"><span className={`repository-preview-status ${versions[0].profile?.processing_status || 'pending'}`}>{versions[0].profile?.processing_status || 'pending'}</span><button className="secondary" onClick={() => void download(versions[0])}><Download />Download</button><button className="icon secondary" aria-label="Close preview" onClick={() => setViewerOpen(false)}><X /></button></div>
        </header>
        <div className="repository-preview-stage"><RepositoryViewer node={selected} version={versions[0]} onChanged={() => void load()} /></div>
      </section>
    </div>}
  </div>
}
