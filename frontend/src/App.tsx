import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, Archive, Boxes, Check, ChevronRight, CircleUserRound, Database,
  FlaskConical, Gauge, KeyRound, LogOut, Menu, Plus, RefreshCw, Search,
  ShieldCheck, UserPlus, UsersRound, X, XCircle,
} from 'lucide-react'
import { api, ApiError } from './api'
import { claims, clearToken, completeLogin, getToken, isOidcConfigured, startLogin } from './auth'
import { isCognitoConfigured, restoreCognitoSession, signInWithPassword, signOutCognito } from './cognito'
import type { Decision, Membership, StoredObject, Study, User } from './types'

type Page = 'overview' | 'studies' | 'memberships' | 'users' | 'decisions' | 'objects'

const nav: { id: Page; label: string; icon: typeof Gauge }[] = [
  { id: 'overview', label: 'Overview', icon: Gauge },
  { id: 'studies', label: 'Study registry', icon: Database },
  { id: 'memberships', label: 'Memberships', icon: UsersRound },
  { id: 'users', label: 'Users', icon: CircleUserRound },
  { id: 'decisions', label: 'Decision lab', icon: FlaskConical },
  { id: 'objects', label: 'Object catalog', icon: Boxes },
]

function message(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} · ${error.code}${error.requestId ? ` · ${error.requestId}` : ''}`
  return error instanceof Error ? error.message : 'Unexpected error'
}

export function App() {
  const [ready, setReady] = useState(false)
  const [user, setUser] = useState<User | null>(null)
  const [studies, setStudies] = useState<Study[]>([])
  const [page, setPage] = useState<Page>(() => (location.hash.slice(1) as Page) || 'overview')
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)

  const notify = useCallback((kind: 'ok' | 'error', text: string) => {
    setNotice({ kind, text })
    window.setTimeout(() => setNotice(null), 5000)
  }, [])

  const load = useCallback(async () => {
    const [me, registry] = await Promise.all([api.me(), api.studies()])
    setUser(me)
    setStudies(registry)
  }, [])

  useEffect(() => {
    const initialize = async () => {
      try {
        if (location.pathname === '/auth/callback') {
          await completeLogin(location.search)
          history.replaceState({}, '', '/')
        }
        if (!getToken()) await restoreCognitoSession()
        if (getToken()) await load()
      } catch (error) {
        clearToken()
        notify('error', message(error))
      } finally {
        setReady(true)
      }
    }
    void initialize()
  }, [load, notify])

  const selectPage = (next: Page) => {
    setPage(next)
    location.hash = next
    setMenuOpen(false)
  }

  if (!ready) return <Centered><Spinner /><p>Establishing secure session…</p></Centered>
  if (!user) return <Login onError={(text) => notify('error', text)} notice={notice} />

  const title = nav.find((item) => item.id === page)?.label || 'Overview'
  return (
    <div className="app-shell">
      <aside className={menuOpen ? 'sidebar open' : 'sidebar'}>
        <div className="brand"><div className="brand-mark"><ShieldCheck /></div><div><strong>Aurinara</strong><span>Platform control plane</span></div></div>
        <nav aria-label="Primary navigation">
          {nav.map((item) => <button key={item.id} className={page === item.id ? 'nav-item active' : 'nav-item'} onClick={() => selectPage(item.id)}><item.icon /><span>{item.label}</span><ChevronRight /></button>)}
        </nav>
        <div className="sidebar-foot">
          <span className="eyebrow">Signed in as</span><strong>{user.display_name || user.email || user.subject}</strong>
          <span className="muted truncate">{user.email || user.issuer}</span>
          <button className="ghost wide" onClick={() => { signOutCognito(); setUser(null) }}><LogOut />Sign out</button>
        </div>
      </aside>
      {menuOpen && <button className="scrim" aria-label="Close menu" onClick={() => setMenuOpen(false)} />}
      <main>
        <header className="topbar"><button className="icon mobile-only" onClick={() => setMenuOpen(true)}><Menu /></button><div><span className="eyebrow">Shared administration</span><h1>{title}</h1></div><div className="environment"><span className="pulse" />Control plane online</div></header>
        <div className="content">
          {page === 'overview' && <Overview user={user} studies={studies} onNavigate={selectPage} />}
          {page === 'studies' && <Studies studies={studies} setStudies={setStudies} notify={notify} />}
          {page === 'memberships' && <Memberships studies={studies} notify={notify} />}
          {page === 'users' && <Users notify={notify} />}
          {page === 'decisions' && <Decisions currentUser={user} studies={studies} notify={notify} />}
          {page === 'objects' && <Objects notify={notify} />}
        </div>
      </main>
      {notice && <div role="status" className={`toast ${notice.kind}`} >{notice.kind === 'ok' ? <Check /> : <XCircle />}<span>{notice.text}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    </div>
  )
}

function Login({ onError, notice }: { onError: (text: string) => void; notice: { kind: string; text: string } | null }) {
  const identity = claims()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const passwordLogin = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    try {
      await signInWithPassword(email, password)
      location.reload()
    } catch (error) {
      onError(message(error))
      setSubmitting(false)
    }
  }
  return <div className="login-page"><div className="login-orbit orbit-one"/><div className="login-orbit orbit-two"/><section className="login-card">
    <div className="brand-mark large"><ShieldCheck /></div><span className="eyebrow">Aurinara shared services</span><h1>Platform Console</h1>
    <p>Govern canonical studies, identities, authorization, and immutable objects from one secure control surface.</p>
    {identity && <p className="inline-note">Session found for {identity.email || identity.sub}. Refresh to continue.</p>}
    {isCognitoConfigured() && <form className="login-form" onSubmit={(event) => void passwordLogin(event)}>
      <Field label="Email"><input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required placeholder="you@example.com" /></Field>
      <Field label="Password"><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></Field>
      <button className="primary login-button" disabled={submitting}><KeyRound />{submitting ? 'Signing in…' : 'Sign in'}</button>
    </form>}
    {isCognitoConfigured() && isOidcConfigured() && <div className="login-divider"><span>or</span></div>}
    {isOidcConfigured() && <button className={isCognitoConfigured() ? 'secondary login-button sso' : 'primary login-button'} onClick={() => void startLogin().catch((error) => onError(message(error)))}><ShieldCheck />Continue with single sign-on</button>}
    <div className="login-features"><span><Check />OIDC + PKCE</span><span><Check />Fail-closed authorization</span><span><Check />No bucket credentials</span></div>
    {notice && <div className="form-error">{notice.text}</div>}
  </section><footer>Private administrative surface · Access is audited and policy controlled</footer></div>
}

function Overview({ user, studies, onNavigate }: { user: User; studies: Study[]; onNavigate: (page: Page) => void }) {
  const active = studies.filter((study) => study.status === 'active').length
  return <>
    <section className="hero"><div><span className="eyebrow">Operational posture</span><h2>One authority. Clear boundaries.</h2><p>Identity, canonical studies, policy decisions, and shared object metadata stay centralized while product data remains independently owned.</p></div><div className="hero-shield"><ShieldCheck /></div></section>
    <div className="metric-grid">
      <Metric label="Visible studies" value={String(studies.length)} note={`${active} active`} icon={<Database />} />
      <Metric label="Account state" value={user.status} note={`Revision ${user.revision}`} icon={<CircleUserRound />} tone={user.status === 'active' ? 'green' : 'amber'} />
      <Metric label="Decision cache" value="≤ 60s" note="Event-invalidated" icon={<Activity />} />
      <Metric label="Object access" value="Gateway" note="Short-lived URLs" icon={<Boxes />} />
    </div>
    <div className="two-column"><Panel title="Registry snapshot" subtitle="Canonical identities visible to your account" action={<button className="ghost" onClick={() => onNavigate('studies')}>Open registry<ChevronRight /></button>}>
      <div className="study-stack">{studies.slice(0, 5).map((study) => <div className="study-row" key={study.id}><div className="code-tile">{study.code.slice(0, 2).toUpperCase()}</div><div><strong>{study.display_name}</strong><span>{study.code}</span></div><Status value={study.status} /></div>)}{studies.length === 0 && <Empty text="No visible studies" />}</div>
    </Panel><Panel title="Platform principles" subtitle="Runtime safety guarantees"><ul className="principles"><li><ShieldCheck /><div><strong>Default deny</strong><span>Outages never restore revoked access.</span></div></li><li><KeyRound /><div><strong>Identity-only tokens</strong><span>Permissions are evaluated at request time.</span></div></li><li><Boxes /><div><strong>Opaque object identity</strong><span>Applications never persist storage keys.</span></div></li></ul></Panel></div>
  </>
}

function Studies({ studies, setStudies, notify }: { studies: Study[]; setStudies: (value: Study[]) => void; notify: Notify }) {
  const [query, setQuery] = useState(''); const [creating, setCreating] = useState(false); const [busy, setBusy] = useState(false)
  const filtered = studies.filter((study) => `${study.code} ${study.display_name}`.toLowerCase().includes(query.toLowerCase()))
  const create = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setBusy(true); const data = new FormData(event.currentTarget)
    try { const study = await api.createStudy({ code: String(data.get('code')), display_name: String(data.get('name')) }); setStudies([...studies, study].sort((a,b) => a.code.localeCompare(b.code))); setCreating(false); notify('ok', `${study.code} registered`) } catch (error) { notify('error', message(error)) } finally { setBusy(false) } }
  const toggle = async (study: Study) => { try { const updated = await api.patchStudy(study, { status: study.status === 'active' ? 'archived' : 'active' }); setStudies(studies.map((item) => item.id === updated.id ? updated : item)); notify('ok', `${updated.code} is ${updated.status}`) } catch (error) { notify('error', message(error)) } }
  return <Panel title="Canonical study registry" subtitle="Globally unique identities shared by Aurinara products" action={<button className="primary" onClick={() => setCreating(!creating)}><Plus />Register study</button>}>
    {creating && <form className="inline-form" onSubmit={(event) => void create(event)}><Field label="Study code"><input name="code" required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,99}" placeholder="STUDY-2026-01" /></Field><Field label="Display name"><input name="name" required placeholder="Protocol display name" /></Field><button disabled={busy} className="primary">{busy ? 'Registering…' : 'Register'}</button></form>}
    <div className="toolbar"><div className="search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search studies" /></div><span>{filtered.length} records</span></div>
    <div className="table-wrap"><table><thead><tr><th>Study</th><th>Canonical ID</th><th>Status</th><th>Revision</th><th /></tr></thead><tbody>{filtered.map((study) => <tr key={study.id}><td><strong>{study.display_name}</strong><span>{study.code}</span></td><td><code>{study.id}</code></td><td><Status value={study.status} /></td><td>v{study.revision}</td><td><button className="ghost" onClick={() => void toggle(study)}><Archive />{study.status === 'active' ? 'Archive' : 'Activate'}</button></td></tr>)}</tbody></table></div>
  </Panel>
}

function Memberships({ studies, notify }: { studies: Study[]; notify: Notify }) {
  const [studyId, setStudyId] = useState(studies[0]?.id || ''); const [items, setItems] = useState<Membership[]>([]); const [loading, setLoading] = useState(false)
  const refresh = useCallback(async () => { if (!studyId) return; setLoading(true); try { setItems(await api.memberships(studyId)) } catch (error) { notify('error', message(error)) } finally { setLoading(false) } }, [studyId, notify])
  useEffect(() => { void refresh() }, [refresh])
  const save = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const data = new FormData(event.currentTarget); const roles = String(data.get('roles')).split(',').map((value) => value.trim()).filter(Boolean)
    try { await api.replaceMembership(studyId, String(data.get('user')), roles, String(data.get('site') || '')); await refresh(); event.currentTarget.reset(); notify('ok', 'Membership replaced and revocation event queued') } catch (error) { notify('error', message(error)) } }
  const remove = async (item: Membership) => { try { await api.removeMembership(item.study_id, item.user_id, item.site_id); await refresh(); notify('ok', 'Membership removed') } catch (error) { notify('error', message(error)) } }
  return <><Panel title="Study memberships" subtitle="Assignments replace the selected user and scope atomically" action={<select value={studyId} onChange={(e) => setStudyId(e.target.value)}>{studies.map((study) => <option key={study.id} value={study.id}>{study.code} · {study.display_name}</option>)}</select>}>
    <form className="membership-form" onSubmit={(event) => void save(event)}><Field label="Global user ID"><input name="user" required placeholder="UUID" /></Field><Field label="Role IDs"><input name="roles" required placeholder="UUID, UUID" /></Field><Field label="Optional site ID"><input name="site" placeholder="UUID" /></Field><button className="primary"><UserPlus />Replace assignment</button></form>
    {loading ? <Spinner /> : <div className="table-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Product</th><th>Scope</th><th /></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><code>{item.user_id}</code></td><td><strong>{item.role_name}</strong><span>{item.role_id}</span></td><td>{item.product}</td><td>{item.site_id ? `Site · ${item.site_id}` : 'Study-wide'}</td><td><button className="danger-link" onClick={() => void remove(item)}>Remove</button></td></tr>)}</tbody></table>{items.length === 0 && <Empty text="No assignments for this study" />}</div>}
  </Panel><Callout>Role definitions are deployed through reviewed migration snapshots. This console assigns existing role UUIDs and never silently unions grants.</Callout></>
}

function Users({ notify }: { notify: Notify }) {
  const [created, setCreated] = useState<User | null>(null)
  const create = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const data = new FormData(event.currentTarget); try { const user = await api.createUser({ issuer: String(data.get('issuer')), subject: String(data.get('subject')), email: String(data.get('email')), display_name: String(data.get('name')) }); setCreated(user); notify('ok', 'Identity registered') } catch (error) { notify('error', message(error)) } }
  const update = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const data = new FormData(event.currentTarget); try { const user = await api.patchUser(String(data.get('id')), Number(data.get('revision')), { status: String(data.get('status')) }); setCreated(user); notify('ok', `Account ${user.status}`) } catch (error) { notify('error', message(error)) } }
  return <div className="two-column"><Panel title="Register identity" subtitle="Map an immutable issuer and subject to a global user"><form className="stack-form" onSubmit={(event) => void create(event)}><Field label="OIDC issuer"><input name="issuer" required placeholder="https://identity.example.com" /></Field><Field label="Immutable subject"><input name="subject" required /></Field><Field label="Email"><input name="email" required type="email" /></Field><Field label="Display name"><input name="name" required /></Field><button className="primary"><UserPlus />Register user</button></form></Panel>
    <Panel title="Account status" subtitle="Deactivate access globally using optimistic concurrency"><form className="stack-form" onSubmit={(event) => void update(event)}><Field label="Global user ID"><input name="id" required defaultValue={created?.id} /></Field><Field label="Current revision"><input name="revision" required type="number" min="1" defaultValue={created?.revision || 1} /></Field><Field label="New status"><select name="status"><option value="inactive">Inactive</option><option value="active">Active</option></select></Field><button className="secondary">Update status</button></form>{created && <div className="result-card"><span className="eyebrow">Last identity</span><strong>{created.display_name}</strong><code>{created.id}</code><Status value={created.status} /></div>}</Panel></div>
}

function Decisions({ currentUser, studies, notify }: { currentUser: User; studies: Study[]; notify: Notify }) {
  const [result, setResult] = useState<Decision | null>(null)
  const submit = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const data = new FormData(event.currentTarget); try { setResult(await api.decide({ principal: { issuer: String(data.get('issuer')), subject: String(data.get('subject')) }, action: String(data.get('action')), resource: { product: String(data.get('product')), type: String(data.get('type')), id: String(data.get('resourceId')) || null, study_id: String(data.get('study')) || null, site_id: String(data.get('site')) || null } })) } catch (error) { notify('error', message(error)) } }
  return <div className="decision-layout"><Panel title="Authorization decision lab" subtitle="Exercise the same centralized policy contract used by product APIs"><form className="stack-form" onSubmit={(event) => void submit(event)}><div className="form-grid"><Field label="Issuer"><input name="issuer" required defaultValue={currentUser.issuer} /></Field><Field label="Subject"><input name="subject" required defaultValue={currentUser.subject} /></Field><Field label="Action"><input name="action" required defaultValue="platform.study.read" /></Field><Field label="Product"><input name="product" required defaultValue="platform" /></Field><Field label="Resource type"><input name="type" required defaultValue="study" /></Field><Field label="Resource ID"><input name="resourceId" /></Field><Field label="Study"><select name="study"><option value="">System scope</option>{studies.map((study) => <option key={study.id} value={study.id}>{study.code}</option>)}</select></Field><Field label="Optional site"><input name="site" /></Field></div><button className="primary"><ShieldCheck />Evaluate policy</button></form></Panel>
    <Panel title="Decision result" subtitle="Stable evidence for audit correlation">{result ? <div className={result.allowed ? 'decision allowed' : 'decision denied'}><div className="decision-icon">{result.allowed ? <Check /> : <X />}</div><span className="eyebrow">{result.allowed ? 'Allowed' : 'Denied'}</span><h3>{result.reason_code.replaceAll('_', ' ')}</h3><dl><dt>Decision ID</dt><dd><code>{result.decision_id}</code></dd><dt>Policy version</dt><dd>{result.policy_version}</dd><dt>Expires</dt><dd>{new Date(result.expires_at).toLocaleString()}</dd></dl></div> : <Empty text="Submit a policy request to inspect the result" />}</Panel></div>
}

function Objects({ notify }: { notify: Notify }) {
  const [record, setRecord] = useState<StoredObject | null>(null); const [id, setId] = useState('')
  const lookup = async (event: FormEvent) => { event.preventDefault(); try { setRecord(await api.object(id)) } catch (error) { notify('error', message(error)); setRecord(null) } }
  const download = async () => { if (!record) return; try { const result = await api.download(record.id); window.location.assign(result.url) } catch (error) { notify('error', message(error)) } }
  return <Panel title="Object catalog" subtitle="Resolve opaque IDs without exposing bucket keys" action={<span className="security-pill"><KeyRound />Authorized metadata only</span>}><form className="object-search" onSubmit={(event) => void lookup(event)}><div className="search"><Search /><input value={id} onChange={(event) => setId(event.target.value)} required placeholder="Object UUID" /></div><button className="primary">Look up</button></form>
    {record ? <div className="object-detail"><div className="object-heading"><div className="code-tile large"><Boxes /></div><div><span className="eyebrow">{record.owner_product} · {record.classification}</span><h3>{record.media_type}</h3><code>{record.id}</code></div><Status value={record.status} /></div><div className="detail-grid"><Detail label="Study ID" value={record.study_id} mono /><Detail label="Size" value={formatBytes(record.size_bytes)} /><Detail label="SHA-256" value={record.sha256 || 'Pending verification'} mono /><Detail label="Retention" value={record.retention_state} /><Detail label="Created" value={new Date(record.created_at).toLocaleString()} /><Detail label="Finalized" value={record.finalized_at ? new Date(record.finalized_at).toLocaleString() : 'Not finalized'} /></div><button className="primary" disabled={record.status !== 'available'} onClick={() => void download()}><KeyRound />Authorize short-lived download</button></div> : <Empty text="Enter an opaque object ID to inspect authorized metadata" />}
  </Panel>
}

type Notify = (kind: 'ok' | 'error', text: string) => void
function Panel({ title, subtitle, action, children }: { title: string; subtitle?: string; action?: ReactNode; children: ReactNode }) { return <section className="panel"><header><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</header><div className="panel-body">{children}</div></section> }
function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="field"><span>{label}</span>{children}</label> }
function Metric({ label, value, note, icon, tone = '' }: { label: string; value: string; note: string; icon: ReactNode; tone?: string }) { return <div className={`metric ${tone}`}><div className="metric-icon">{icon}</div><span>{label}</span><strong>{value}</strong><small>{note}</small></div> }
function Status({ value }: { value: string }) { return <span className={`status ${['active','available'].includes(value) ? 'positive' : value === 'pending' ? 'pending' : 'neutral'}`}><i />{value}</span> }
function Empty({ text }: { text: string }) { return <div className="empty"><Database /><span>{text}</span></div> }
function Callout({ children }: { children: ReactNode }) { return <div className="callout"><ShieldCheck />{children}</div> }
function Detail({ label, value, mono }: { label: string; value: string; mono?: boolean }) { return <div><span>{label}</span><strong className={mono ? 'mono' : ''}>{value}</strong></div> }
function Centered({ children }: { children: ReactNode }) { return <div className="centered">{children}</div> }
function Spinner() { return <RefreshCw className="spinner" /> }
export function formatBytes(value: number | null): string { if (value === null) return 'Pending verification'; if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB` }
