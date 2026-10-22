import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import { api, ApiError, waitForRepositoryJob } from './api'
import type { PdfAnnotation, PdfAnnotationStyle, RepositoryNode, RepositoryVersion, TabularPreview } from './types'

declare global {
  interface Window { DocsAPI?: { DocEditor: new (id: string, options: Record<string, unknown>) => { destroyEditor: () => void } } }
}

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : 'Preview failed'
}

const DEFAULT_ANNOTATION_STYLE: PdfAnnotationStyle = {
  font_family: 'Helvetica', font_size: 10, text_color: '#1f2937',
  fill_color: '#facc15', border_color: '#d97706', opacity: 0.32,
}

type AnnotationDraft = {
  id?: string
  revision?: number
  geometry: PdfAnnotation['geometry']
  selected_text: string
  comment: string
  category: string
  status: string
  style: PdfAnnotationStyle
}

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value))
const colorWithOpacity = (color: string, opacity: number) => `${color}${Math.round(clamp(opacity, 0, 1) * 255).toString(16).padStart(2, '0')}`
const annotationDraft = (item?: PdfAnnotation): AnnotationDraft => item ? {
  id: item.id, revision: item.revision, geometry: { ...item.geometry },
  selected_text: item.selected_text || '', comment: item.comment || '', category: item.category,
  status: item.status, style: { ...DEFAULT_ANNOTATION_STYLE, ...item.style },
} : {
  geometry: { x: 0.08, y: 0.08, width: 0.34, height: 0.1 }, selected_text: '', comment: '',
  category: 'comment', status: 'open', style: { ...DEFAULT_ANNOTATION_STYLE },
}

export function RepositoryViewer({ node, version, onChanged }: { node: RepositoryNode; version: RepositoryVersion; onChanged: () => void }) {
  const kind = version.profile?.file_kind
  if (version.profile?.processing_status === 'pending') return <div className="repo-preview-state">Preparing preview…</div>
  if (version.profile?.processing_status === 'failed') return <div className="repo-preview-state">Preview failed ({version.profile.failure_code}). The original file is still downloadable.</div>
  if (kind === 'pdf') return <PdfViewer node={node} version={version} onChanged={onChanged} />
  if (kind === 'csv' || kind === 'parquet') return <TabularViewer version={version} />
  if (['docx', 'xlsx', 'pptx'].includes(kind || '')) return <OfficeViewer version={version} />
  return <div className="repo-preview-state">No specialized preview is available for this format.</div>
}

function PdfViewer({ node, version, onChanged }: { node: RepositoryNode; version: RepositoryVersion; onChanged: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const pageRef = useRef<HTMLDivElement>(null)
  const [pdf, setPdf] = useState<PDFDocumentProxy>()
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState(0)
  const [annotations, setAnnotations] = useState<PdfAnnotation[]>([])
  const [draft, setDraft] = useState<AnnotationDraft>()
  const [drawing, setDrawing] = useState(false)
  const [drawStart, setDrawStart] = useState<{ x: number; y: number }>()
  const [saving, setSaving] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [failure, setFailure] = useState('')
  const dragRef = useRef<{ id: string; pointerId: number; offsetX: number; offsetY: number; moved: boolean } | undefined>(undefined)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [{ url }, pdfjs, workerUrl] = await Promise.all([
          api.downloadRepositoryVersion(version.id),
          import('pdfjs-dist'),
          import('pdfjs-dist/build/pdf.worker.min.mjs?url'),
        ])
        pdfjs.GlobalWorkerOptions.workerSrc = workerUrl.default
        const document = await pdfjs.getDocument({ url, disableRange: false, disableStream: false }).promise
        if (!cancelled) setPdf(document)
      } catch (error) { if (!cancelled) setFailure(errorMessage(error)) }
    })()
    return () => { cancelled = true; void pdf?.cleanup() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version.id])

  useEffect(() => {
    if (!pdf || !canvasRef.current) return
    let cancelled = false
    void pdf.getPage(page).then(async (value) => {
      if (cancelled || !canvasRef.current) return
      const viewport = value.getViewport({ scale: 1.25 })
      const canvas = canvasRef.current
      canvas.width = viewport.width
      canvas.height = viewport.height
      const context = canvas.getContext('2d')
      if (context) await value.render({ canvas, canvasContext: context, viewport }).promise
    }).catch((error) => setFailure(errorMessage(error)))
    return () => { cancelled = true }
  }, [pdf, page])

  useEffect(() => {
    void api.annotations(version.id).then((values) => {
      setAnnotations(values)
      const linked = new URLSearchParams(location.search).get('annotation')
      const target = values.find((item) => item.id === linked)
      if (target) setPage(target.page_number)
    }).catch(() => setAnnotations([]))
  }, [version.id])

  const search = async () => {
    if (!pdf || !query.trim()) { setMatches(0); return }
    let count = 0
    for (let index = 1; index <= pdf.numPages; index += 1) {
      const content = await (await pdf.getPage(index)).getTextContent()
      const text = content.items.map((item) => 'str' in item ? item.str : '').join(' ').toLocaleLowerCase()
      if (text.includes(query.toLocaleLowerCase())) { count += 1; if (count === 1) setPage(index) }
    }
    setMatches(count)
  }

  const startAnnotation = () => {
    setDraft(annotationDraft())
    setDrawing(true)
    setConfirmDelete(false)
    setFailure('Drag on the page to place the annotation, then fine-tune it in the panel.')
  }
  const point = (event: { clientX: number; clientY: number }) => {
    const bounds = pageRef.current?.getBoundingClientRect()
    if (!bounds) return { x: 0, y: 0 }
    return { x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1), y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1) }
  }
  const beginAnnotationDrag = (event: ReactPointerEvent<HTMLButtonElement>, item: PdfAnnotation) => {
    event.stopPropagation()
    setDraft(annotationDraft(item))
    setDrawing(false)
    setConfirmDelete(false)
    setFailure('')
    if (!version.capabilities.can_annotate) return
    const current = point(event)
    dragRef.current = { id: item.id, pointerId: event.pointerId, offsetX: current.x - item.geometry.x, offsetY: current.y - item.geometry.y, moved: false }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const moveAnnotation = (event: ReactPointerEvent<HTMLButtonElement>, item: PdfAnnotation) => {
    const drag = dragRef.current
    if (!drag || drag.id !== item.id || drag.pointerId !== event.pointerId) return
    event.stopPropagation()
    const current = point(event)
    const x = clamp(current.x - drag.offsetX, 0, 1 - item.geometry.width)
    const y = clamp(current.y - drag.offsetY, 0, 1 - item.geometry.height)
    if (Math.abs(x - item.geometry.x) > 0.002 || Math.abs(y - item.geometry.y) > 0.002) drag.moved = true
    setDraft((value) => value?.id === item.id ? { ...value, geometry: { ...value.geometry, x, y } } : value)
  }
  const endAnnotationDrag = (event: ReactPointerEvent<HTMLButtonElement>, item: PdfAnnotation) => {
    const drag = dragRef.current
    if (!drag || drag.id !== item.id || drag.pointerId !== event.pointerId) return
    event.stopPropagation()
    dragRef.current = undefined
    if (drag.moved) setFailure('Position updated. Save changes to share it with other users.')
  }
  const beginDraw = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drawing) return
    const start = point(event)
    event.currentTarget.setPointerCapture(event.pointerId)
    setDrawStart(start)
    setDraft((value) => ({ ...(value || annotationDraft()), geometry: { x: start.x, y: start.y, width: 0.01, height: 0.01 } }))
  }
  const moveDraw = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drawing || !drawStart) return
    const current = point(event)
    const geometry = {
      x: Math.min(drawStart.x, current.x), y: Math.min(drawStart.y, current.y),
      width: Math.max(0.01, Math.abs(current.x - drawStart.x)), height: Math.max(0.01, Math.abs(current.y - drawStart.y)),
    }
    setDraft((value) => value ? { ...value, geometry: { ...geometry, width: Math.min(geometry.width, 1 - geometry.x), height: Math.min(geometry.height, 1 - geometry.y) } } : value)
  }
  const endDraw = () => { if (drawStart) { setDrawStart(undefined); setDrawing(false); setFailure('') } }
  const updateGeometry = (field: keyof PdfAnnotation['geometry'], percent: number) => setDraft((value) => {
    if (!value) return value
    const next = { ...value.geometry, [field]: clamp(percent / 100, 0, 1) }
    next.x = clamp(next.x, 0, 0.99); next.y = clamp(next.y, 0, 0.99)
    next.width = clamp(next.width, 0.01, 1 - next.x); next.height = clamp(next.height, 0.01, 1 - next.y)
    return { ...value, geometry: next }
  })
  const saveAnnotation = async () => {
    if (!draft?.comment.trim()) { setFailure('Annotation text is required.'); return }
    setSaving(true); setFailure('')
    try {
      const payload = { geometry: draft.geometry, selected_text: draft.selected_text.trim() || null, comment: draft.comment.trim(), category: draft.category, status: draft.status, style: draft.style }
      if (draft.id) {
        const current = annotations.find((item) => item.id === draft.id)
        if (!current) return
        const updated = await api.patchAnnotation(current, payload)
        setAnnotations((items) => items.map((item) => item.id === updated.id ? updated : item))
      } else {
        const created = await api.createAnnotation(version.id, { page_number: page, ...payload })
        setAnnotations((items) => [...items, created])
      }
      setDraft(undefined); setConfirmDelete(false)
    } catch (error) { setFailure(errorMessage(error)) } finally { setSaving(false) }
  }
  const removeAnnotation = useCallback(async (annotation: PdfAnnotation) => {
    try {
      await api.deleteAnnotation(annotation)
      setAnnotations((values) => values.filter((item) => item.id !== annotation.id))
      setDraft(undefined); setConfirmDelete(false)
    } catch (error) { setFailure(errorMessage(error)) }
  }, [])
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.matches('input, textarea, select, [contenteditable="true"]')) return
      if (event.key === 'Escape' && draft) {
        setDraft(undefined); setDrawing(false); setConfirmDelete(false); setFailure('')
        return
      }
      if ((event.key !== 'Delete' && event.key !== 'Backspace') || !draft?.id || saving || !version.capabilities.can_annotate) return
      const item = annotations.find((value) => value.id === draft.id)
      if (!item) return
      event.preventDefault()
      if (confirmDelete) void removeAnnotation(item)
      else { setConfirmDelete(true); setFailure('Press Delete or Backspace again to remove this shared annotation.') }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [annotations, confirmDelete, draft, removeAnnotation, saving, version.capabilities.can_annotate])
  const exportAnnotated = async () => {
    await waitForRepositoryJob(await api.annotatedExport(version.id, node.display_name.replace(/\.pdf$/i, '') + '-annotated.pdf'))
    onChanged()
  }
  const copyLink = (annotation: PdfAnnotation) => navigator.clipboard.writeText(`${location.origin}${location.pathname}?study=${node.study_id}&version=${version.id}&annotation=${annotation.id}`)

  if (failure && !pdf) return <div className="repo-preview-state">{failure}</div>
  const pageAnnotations = annotations.filter((item) => item.page_number === page)
  const annotationNavigation = [...annotations].sort((left, right) => left.page_number - right.page_number || left.id.localeCompare(right.id))
  const annotationStyle = (value: AnnotationDraft | PdfAnnotation) => {
    const style = { ...DEFAULT_ANNOTATION_STYLE, ...value.style }
    return { left: `${value.geometry.x * 100}%`, top: `${value.geometry.y * 100}%`, width: `${value.geometry.width * 100}%`, height: `${value.geometry.height * 100}%`, backgroundColor: colorWithOpacity(style.fill_color, style.opacity), borderColor: style.border_color, color: style.text_color, fontFamily: style.font_family, fontSize: `${style.font_size}px` }
  }
  return <div className="pdf-viewer">
    <div className="pdf-commandbar">
      <div className="pdf-navigation"><button disabled={page <= 1} onClick={() => { setPage((value) => value - 1); setDraft(undefined) }}>Previous</button><strong>Page {page} <span>/ {pdf?.numPages || '…'}</span></strong><button disabled={!pdf || page >= pdf.numPages} onClick={() => { setPage((value) => value + 1); setDraft(undefined) }}>Next</button></div>
      <div className="pdf-search"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search this PDF"/><button onClick={() => void search()}>Search</button><small>{matches ? `${matches} page matches` : ''}</small></div>
      {version.capabilities.can_annotate && <div className="pdf-actions"><button className={drawing ? 'active' : ''} onClick={startAnnotation}>+ Add annotation</button><button onClick={() => void exportAnnotated()}>Export annotated</button></div>}
    </div>
    {failure && <div className="annotation-message">{failure}</div>}
    <div className="pdf-workspace">
      <div className="pdf-document-scroll">
        <div ref={pageRef} className={`pdf-page${drawing ? ' is-drawing' : ''}`} onPointerDown={beginDraw} onPointerMove={moveDraw} onPointerUp={endDraw} onPointerCancel={endDraw}>
          <canvas ref={canvasRef}/>
          {pageAnnotations.map((item) => {
            const displayed = draft?.id === item.id ? draft : item
            return <button key={item.id} type="button" title="Click to edit. Drag to reposition." className={`pdf-annotation movable${draft?.id === item.id ? ' selected' : ''}`} style={annotationStyle(displayed)} onPointerDown={(event) => beginAnnotationDrag(event, item)} onPointerMove={(event) => moveAnnotation(event, item)} onPointerUp={(event) => endAnnotationDrag(event, item)} onPointerCancel={(event) => endAnnotationDrag(event, item)} onClick={(event) => { event.stopPropagation(); if (event.detail === 0) setDraft(annotationDraft(item)) }}>{displayed.comment}</button>
          })}
          {draft && !draft.id && (!drawing || drawStart) && <div className="pdf-annotation draft" style={annotationStyle(draft)}>{draft.comment || 'Type annotation text →'}</div>}
        </div>
      </div>
      <aside className="annotation-panel">
        <div className="annotation-panel-heading"><div><small>{draft ? `PAGE ${page}` : `${annotations.length} TOTAL`}</small><h3>{draft ? draft.id ? 'Edit annotation' : 'New annotation' : 'Annotation navigation'}</h3></div><span>{draft ? pageAnnotations.length : annotations.length}</span></div>
        <p className="annotation-shortcuts">Click to edit · Drag to move · Delete twice to remove · Esc to close</p>
        {draft ? <div className="annotation-editor">
          <label className="annotation-field wide"><span>Annotation text</span><textarea autoFocus={!draft.id} value={draft.comment} onChange={(event) => setDraft({ ...draft, comment: event.target.value })} placeholder="What should reviewers see?"/></label>
          <label className="annotation-field wide"><span>Referenced PDF text <em>optional</em></span><textarea value={draft.selected_text} onChange={(event) => setDraft({ ...draft, selected_text: event.target.value })} placeholder="Paste the source text this note refers to"/></label>
          <fieldset className="annotation-section"><legend>Position & size <span>percent of page</span></legend><div className="geometry-grid">{(['x', 'y', 'width', 'height'] as const).map((field) => <label key={field}><span>{{ x: 'Left', y: 'Top', width: 'Width', height: 'Height' }[field]}</span><input type="number" min="0" max="100" step="0.5" value={(draft.geometry[field] * 100).toFixed(1)} onChange={(event) => updateGeometry(field, Number(event.target.value))}/></label>)}</div><button className="draw-again" onClick={() => setDrawing(true)}>Draw placement on page</button></fieldset>
          <fieldset className="annotation-section"><legend>Typography</legend><div className="style-grid"><label><span>Font</span><select value={draft.style.font_family} onChange={(event) => setDraft({ ...draft, style: { ...draft.style, font_family: event.target.value as PdfAnnotationStyle['font_family'] } })}><option>Helvetica</option><option>Times-Roman</option><option>Courier</option></select></label><label><span>Size (pt)</span><input type="number" min="6" max="48" value={draft.style.font_size} onChange={(event) => setDraft({ ...draft, style: { ...draft.style, font_size: clamp(Number(event.target.value), 6, 48) } })}/></label></div></fieldset>
          <fieldset className="annotation-section"><legend>Colors</legend><div className="color-grid"><label><span>Text</span><input type="color" value={draft.style.text_color} onChange={(event) => setDraft({ ...draft, style: { ...draft.style, text_color: event.target.value } })}/></label><label><span>Fill</span><input type="color" value={draft.style.fill_color} onChange={(event) => setDraft({ ...draft, style: { ...draft.style, fill_color: event.target.value } })}/></label><label><span>Border</span><input type="color" value={draft.style.border_color} onChange={(event) => setDraft({ ...draft, style: { ...draft.style, border_color: event.target.value } })}/></label></div><label className="opacity-field"><span>Fill opacity</span><input type="range" min="0.05" max="1" step="0.05" value={draft.style.opacity} onChange={(event) => setDraft({ ...draft, style: { ...draft.style, opacity: Number(event.target.value) } })}/><output>{Math.round(draft.style.opacity * 100)}%</output></label></fieldset>
          <div className="style-grid"><label className="annotation-field"><span>Category</span><select value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })}><option value="comment">Comment</option><option value="issue">Issue</option><option value="highlight">Highlight</option><option value="note">Note</option></select></label><label className="annotation-field"><span>Status</span><select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })}><option value="open">Open</option><option value="resolved">Resolved</option></select></label></div>
          <div className="annotation-editor-actions"><button onClick={() => { setDraft(undefined); setDrawing(false); setConfirmDelete(false); setFailure('') }}>Cancel</button><button className="primary" disabled={saving || !draft.comment.trim()} onClick={() => void saveAnnotation()}>{saving ? 'Saving…' : draft.id ? 'Save changes' : 'Create annotation'}</button></div>
          {draft.id && <div className="annotation-secondary-actions"><button onClick={() => { const item = annotations.find((value) => value.id === draft.id); if (item) void copyLink(item) }}>Copy deep link</button>{confirmDelete ? <><span>Delete this shared annotation?</span><button className="danger" onClick={() => { const item = annotations.find((value) => value.id === draft.id); if (item) void removeAnnotation(item) }}>Delete</button><button onClick={() => setConfirmDelete(false)}>Keep</button></> : <button className="danger-link" onClick={() => setConfirmDelete(true)}>Delete annotation</button>}</div>}
        </div> : <div className="annotation-navigation">
          <div className="annotation-navigation-intro"><div><strong>Review the document</strong><p>Select a note to jump to its page and edit it.</p></div>{version.capabilities.can_annotate && <button className="primary" onClick={startAnnotation}>+ Add</button>}</div>
          {annotationNavigation.length ? <div className="annotation-list">{annotationNavigation.map((item) => <button key={item.id} className={item.page_number === page ? 'current-page' : ''} onClick={() => { setPage(item.page_number); setDraft(annotationDraft(item)); setDrawing(false); setConfirmDelete(false); setFailure('') }}><span className="annotation-list-color" style={{ background: item.style?.border_color || DEFAULT_ANNOTATION_STYLE.border_color }}/><span className="annotation-list-page">P{item.page_number}</span><span className="annotation-list-copy"><strong>{item.comment || 'Untitled annotation'}</strong><small>{item.category} · {item.status}</small></span></button>)}</div> : <div className="annotation-empty"><strong>No annotations yet</strong><p>Add a precisely positioned review note to start the document conversation.</p>{version.capabilities.can_annotate && <button onClick={startAnnotation}>Create annotation</button>}</div>}
        </div>}
      </aside>
    </div>
  </div>
}

function TabularViewer({ version }: { version: RepositoryVersion }) {
  const [preview, setPreview] = useState<TabularPreview>()
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState('')
  const [sort, setSort] = useState('')
  const [delimiter, setDelimiter] = useState('')
  const [encoding, setEncoding] = useState('')
  const [failure, setFailure] = useState('')
  const firstColumn = preview?.schema[0]?.name
  useEffect(() => {
    const body: Record<string, unknown> = { page, page_size: 100, filters: [], sort: [] }
    if (filter && firstColumn) body.filters = [{ column: firstColumn, operator: 'contains', value: filter }]
    if (sort) body.sort = [{ column: sort, direction: 'asc' }]
    if (version.profile?.file_kind === 'csv' && (delimiter || encoding)) {
      body.csv = { ...(delimiter ? { delimiter } : {}), ...(encoding ? { encoding } : {}) }
    }
    void api.tabularPreview(version.id, body).then(setPreview).catch((error) => setFailure(errorMessage(error)))
  }, [version.id, version.profile?.file_kind, page, filter, sort, delimiter, encoding, firstColumn])
  if (failure) return <div className="repo-preview-state">{failure}</div>
  return <div className="tabular-viewer"><div className="viewer-toolbar"><input value={filter} onChange={(event) => { setPage(1); setFilter(event.target.value) }} placeholder="Filter first column"/><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="">No sort</option>{preview?.schema.map((column) => <option key={column.name}>{column.name}</option>)}</select>{version.profile?.file_kind === 'csv' && <><select aria-label="CSV delimiter" value={delimiter} onChange={(event) => setDelimiter(event.target.value)}><option value="">Detected delimiter</option><option value=",">Comma</option><option value=";">Semicolon</option><option value={"\t"}>Tab</option><option value="|">Pipe</option></select><select aria-label="CSV encoding" value={encoding} onChange={(event) => setEncoding(event.target.value)}><option value="">Detected encoding</option><option value="utf-8">UTF-8</option><option value="utf-8-sig">UTF-8 BOM</option><option value="windows-1252">Windows-1252</option></select></>}<button disabled={page === 1} onClick={() => setPage((value) => value - 1)}>Previous</button><span>Page {page}</span><button disabled={!preview || page * preview.page_size >= preview.total_rows} onClick={() => setPage((value) => value + 1)}>Next</button><small>{preview?.total_rows.toLocaleString()} rows</small></div><div className="table-scroll"><table><thead><tr>{preview?.schema.map((column) => <th key={column.name}>{column.name}<small>{column.type}</small></th>)}</tr></thead><tbody>{preview?.rows.map((row, index) => <tr key={index}>{preview.schema.map((column) => <td key={column.name}>{String(row[column.name] ?? '')}</td>)}</tr>)}</tbody></table></div></div>
}

function OfficeViewer({ version }: { version: RepositoryVersion }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const editorRef = useRef<{ destroyEditor: () => void }>()
  const [mode, setMode] = useState<'view' | 'edit'>(version.capabilities.can_edit ? 'edit' : 'view')
  const [failure, setFailure] = useState('')
  useEffect(() => {
    let disposed = false
    void api.officeSession(version.id, mode).then(({ document_server_url, config }) => new Promise<void>((resolve, reject) => {
      const start = () => {
        if (disposed || !hostRef.current || !window.DocsAPI) return
        editorRef.current?.destroyEditor()
        editorRef.current = new window.DocsAPI.DocEditor(hostRef.current.id, config)
        resolve()
      }
      if (window.DocsAPI) { start(); return }
      const script = document.createElement('script')
      script.src = `${document_server_url.replace(/\/$/, '')}/web-apps/apps/api/documents/api.js`
      script.onload = start
      script.onerror = () => reject(new Error('ONLYOFFICE is unavailable'))
      document.head.appendChild(script)
    })).catch((error) => setFailure(errorMessage(error)))
    return () => { disposed = true; editorRef.current?.destroyEditor(); editorRef.current = undefined }
  }, [version.id, mode])
  return <div className="office-viewer"><div className="viewer-toolbar">{version.capabilities.can_edit && <select value={mode} onChange={(event) => setMode(event.target.value as 'view' | 'edit')}><option value="view">View</option><option value="edit">Edit collaboratively</option></select>}<span>{failure}</span></div><div id={`onlyoffice-${version.id}`} ref={hostRef} className="office-frame"/></div>
}
