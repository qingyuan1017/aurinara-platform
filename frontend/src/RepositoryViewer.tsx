import { useEffect, useRef, useState } from 'react'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import { api, ApiError, waitForRepositoryJob } from './api'
import type { PdfAnnotation, RepositoryNode, RepositoryVersion, TabularPreview } from './types'

declare global {
  interface Window { DocsAPI?: { DocEditor: new (id: string, options: Record<string, unknown>) => { destroyEditor: () => void } } }
}

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : 'Preview failed'
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
  const [pdf, setPdf] = useState<PDFDocumentProxy>()
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [matches, setMatches] = useState(0)
  const [annotations, setAnnotations] = useState<PdfAnnotation[]>([])
  const [failure, setFailure] = useState('')

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

  const addAnnotation = async () => {
    const comment = prompt('Shared annotation comment')?.trim()
    if (!comment) return
    const created = await api.createAnnotation(version.id, { page_number: page, geometry: { x: 0.08, y: 0.08, width: 0.35, height: 0.06 }, comment, category: 'comment', status: 'open' })
    setAnnotations((values) => [...values, created])
  }
  const removeAnnotation = async (annotation: PdfAnnotation) => {
    await api.deleteAnnotation(annotation)
    setAnnotations((values) => values.filter((item) => item.id !== annotation.id))
  }
  const exportAnnotated = async () => {
    await waitForRepositoryJob(await api.annotatedExport(version.id, node.display_name.replace(/\.pdf$/i, '') + '-annotated.pdf'))
    onChanged()
  }
  const copyLink = (annotation: PdfAnnotation) => navigator.clipboard.writeText(`${location.origin}${location.pathname}?study=${node.study_id}&version=${version.id}&annotation=${annotation.id}`)

  if (failure) return <div className="repo-preview-state">{failure}</div>
  return <div className="pdf-viewer">
    <div className="viewer-toolbar"><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>Previous</button><span>Page {page} / {pdf?.numPages || '…'}</span><button disabled={!pdf || page >= pdf.numPages} onClick={() => setPage((value) => value + 1)}>Next</button><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search PDF text"/><button onClick={() => void search()}>Search</button><small>{matches ? `${matches} page matches` : ''}</small>{version.capabilities.can_annotate && <><button onClick={() => void addAnnotation()}>Annotate</button><button onClick={() => void exportAnnotated()}>Export annotated</button></>}</div>
    <div className="pdf-stage"><div className="pdf-page"><canvas ref={canvasRef}/>{annotations.filter((item) => item.page_number === page).map((item) => <button key={item.id} title={item.comment || 'Annotation'} className="pdf-annotation" style={{ left: `${item.geometry.x * 100}%`, top: `${item.geometry.y * 100}%`, width: `${item.geometry.width * 100}%`, height: `${item.geometry.height * 100}%` }} onDoubleClick={() => void removeAnnotation(item)} />)}</div><aside>{annotations.filter((item) => item.page_number === page).map((item) => <div key={item.id}><strong>{item.category}</strong><p>{item.comment}</p><button onClick={() => void copyLink(item)}>Copy link</button><button onClick={() => void removeAnnotation(item)}>Delete</button></div>)}</aside></div>
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
