import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, Archive, Boxes, Check, ChevronRight, CircleUserRound, Database, FolderKanban,
  FlaskConical, Gauge, KeyRound, LogOut, Menu, Plus, RefreshCw, Search,
  ShieldCheck, UserPlus, UsersRound, X, XCircle,
} from 'lucide-react'
import { api, ApiError } from './api'
import { claims, clearToken, completeLogin, getToken, isOidcConfigured, startLogin } from './auth'
import { isCognitoConfigured, restoreCognitoSession, signInWithPassword, signOutCognito } from './cognito'
import type { Decision, Membership, Permission, Role, RoleChange, Site, StoredObject, Study, SystemAssignment, User } from './types'
import { RepositoryPage } from './RepositoryPage'

type Page = 'overview' | 'repository' | 'studies' | 'roles' | 'memberships' | 'access' | 'users' | 'decisions' | 'objects'

const nav: { id: Page; label: string; icon: typeof Gauge }[] = [
  { id: 'overview', label: 'Overview', icon: Gauge },
  { id: 'repository', label: 'Repository', icon: FolderKanban },
  { id: 'studies', label: 'Study registry', icon: Database },
  { id: 'users', label: 'Users', icon: CircleUserRound },
  { id: 'roles', label: 'Roles & permissions', icon: ShieldCheck },
  { id: 'access', label: 'Product access', icon: ShieldCheck },
  { id: 'memberships', label: 'Memberships', icon: UsersRound },
  { id: 'decisions', label: 'Decision lab', icon: FlaskConical },
  { id: 'objects', label: 'Object catalog', icon: Boxes },
]

const membershipProducts = [
  { id: 'platform', label: 'Repository' },
  { id: 'aurinara-edc', label: 'Aurinara EDC' },
  { id: 'aurinara', label: 'Aurinara' },
] as const
type MembershipProduct = (typeof membershipProducts)[number]['id']

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
          const returnTo = await completeLogin(location.search)
          history.replaceState({}, '', returnTo)
          const restoredPage = location.hash.slice(1) as Page
          setPage(nav.some((item) => item.id === restoredPage) ? restoredPage : 'overview')
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
          {page === 'repository' && <RepositoryPage studies={studies} notify={notify} />}
          {page === 'studies' && <Studies studies={studies} setStudies={setStudies} notify={notify} />}
          {page === 'memberships' && <Memberships studies={studies} notify={notify} />}
          {page === 'roles' && <Roles notify={notify} />}
          {page === 'access' && <ProductAccess notify={notify} />}
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
  const managedLoginOnly = import.meta.env.VITE_MANAGED_LOGIN_ONLY === 'true'
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
    {!managedLoginOnly && isCognitoConfigured() && <form className="login-form" onSubmit={(event) => void passwordLogin(event)}>
      <Field label="Email"><input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required placeholder="you@example.com" /></Field>
      <Field label="Password"><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></Field>
      <button className="primary login-button" disabled={submitting}><KeyRound />{submitting ? 'Signing in…' : 'Sign in'}</button>
    </form>}
    {!managedLoginOnly && isCognitoConfigured() && isOidcConfigured() && <div className="login-divider"><span>or</span></div>}
    {isOidcConfigured() && <button className={!managedLoginOnly && isCognitoConfigured() ? 'secondary login-button sso' : 'primary login-button'} onClick={() => void startLogin().catch((error) => onError(message(error)))}><ShieldCheck />Continue with secure sign in</button>}
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
  const filtered = studies.filter((study) => `${study.code} ${study.display_name} ${study.sponsor ?? ''}`.toLowerCase().includes(query.toLowerCase()))
  const create = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setBusy(true); const data = new FormData(event.currentTarget)
    try { const study = await api.createStudy({ code: String(data.get('code')), display_name: String(data.get('name')), sponsor: String(data.get('sponsor')) }); setStudies([...studies, study].sort((a,b) => a.code.localeCompare(b.code))); setCreating(false); notify('ok', `${study.code} registered`) } catch (error) { notify('error', message(error)) } finally { setBusy(false) } }
  const toggle = async (study: Study) => { try { const updated = await api.patchStudy(study, { status: study.status === 'active' ? 'archived' : 'active' }); setStudies(studies.map((item) => item.id === updated.id ? updated : item)); notify('ok', `${updated.code} is ${updated.status}`) } catch (error) { notify('error', message(error)) } }
  return <Panel title="Canonical study registry" subtitle="Globally unique identities shared by Aurinara products" action={<button className="primary" onClick={() => setCreating(!creating)}><Plus />Register study</button>}>
    {creating && <form className="inline-form" onSubmit={(event) => void create(event)}><Field label="Study code"><input name="code" required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,99}" placeholder="STUDY-2026-01" /></Field><Field label="Display name"><input name="name" required placeholder="Protocol display name" /></Field><Field label="Sponsor"><input name="sponsor" required placeholder="Sponsor organization" /></Field><button disabled={busy} className="primary">{busy ? 'Registering…' : 'Register'}</button></form>}
    <div className="toolbar"><div className="search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search studies" /></div><span>{filtered.length} records</span></div>
    <div className="table-wrap"><table><thead><tr><th>Study</th><th>Sponsor</th><th>Canonical ID</th><th>Status</th><th>Revision</th><th /></tr></thead><tbody>{filtered.map((study) => <tr key={study.id}><td><strong>{study.display_name}</strong><span>{study.code}</span></td><td>{study.sponsor ?? 'Not supplied'}</td><td><code>{study.id}</code></td><td><Status value={study.status} /></td><td>v{study.revision}</td><td><button className="ghost" onClick={() => void toggle(study)}><Archive />{study.status === 'active' ? 'Archive' : 'Activate'}</button></td></tr>)}</tbody></table></div>
  </Panel>
}

function Memberships({ studies, notify }: { studies: Study[]; notify: Notify }) {
  const [studyId, setStudyId] = useState(studies[0]?.id || '')
  const [items, setItems] = useState<Membership[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [roles, setRoles] = useState<Role[]>([])
  const [userId, setUserId] = useState('')
  const [product, setProduct] = useState<MembershipProduct>('aurinara-edc')
  const [scope, setScope] = useState<'study' | 'site'>('study')
  const [siteId, setSiteId] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void Promise.all([api.users(), api.roles()])
      .then(([nextUsers, nextRoles]) => {
        setUsers(nextUsers)
        setRoles(nextRoles)
        setUserId((current) => current || nextUsers[0]?.id || '')
      })
      .catch((error) => notify('error', message(error)))
  }, [notify])

  const refresh = useCallback(async () => {
    if (!studyId) return
    setLoading(true)
    try {
      const [nextItems, nextSites] = await Promise.all([
        api.memberships(studyId),
        api.sites(studyId),
      ])
      setItems(nextItems)
      setSites(nextSites)
      setSiteId((current) => nextSites.some((site) => site.id === current) ? current : nextSites[0]?.id || '')
    }
    catch (error) { notify('error', message(error)) }
    finally { setLoading(false) }
  }, [studyId, notify])
  useEffect(() => { void refresh() }, [refresh])

  const scopedRoles = roles.filter((role) => role.product === product && role.scope_level === scope && role.assignable)
  const visibleItems = items.filter((item) => item.product === product)
  const siteNames = new Map(sites.map((site) => [site.id, site.name]))
  const productLabel = membershipProducts.find((item) => item.id === product)?.label || product
  useEffect(() => { setSelected([]) }, [product, scope])

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!studyId || !userId || selected.length === 0) return
    setBusy(true)
    try {
      await api.replaceMembership(studyId, userId, product, selected, scope === 'site' ? siteId : '')
      await refresh()
      notify('ok', `${scope === 'site' ? 'Site' : 'Study'} membership updated`)
    } catch (error) {
      notify('error', message(error))
    } finally {
      setBusy(false)
    }
  }
  const remove = async (item: Membership) => { try { await api.removeMembership(item.study_id, item.user_id, item.product, item.site_id); await refresh(); notify('ok', `${productLabel} membership scope removed`) } catch (error) { notify('error', message(error)) } }

  return <><div className="product-tabs" role="tablist" aria-label="Membership product">
    {membershipProducts.map((item) => <button key={item.id} type="button" role="tab" aria-selected={product === item.id} className={product === item.id ? 'active' : ''} onClick={() => setProduct(item.id)}>{item.label}</button>)}
  </div><Panel title={`${productLabel} study and site memberships`} subtitle="Assign only study- or site-scoped product roles" action={<select value={studyId} onChange={(event) => { setStudyId(event.target.value); setSiteId('') }}>{studies.map((study) => <option key={study.id} value={study.id}>{study.code} · {study.display_name}</option>)}</select>}>
    <form className="membership-editor" onSubmit={(event) => void save(event)}>
      <div className="membership-fields"><Field label="User"><select required value={userId} onChange={(event) => setUserId(event.target.value)}>{users.map((user) => <option key={user.id} value={user.id}>{user.display_name || user.email || user.subject}</option>)}</select></Field><Field label="Assignment scope"><select value={scope} onChange={(event) => setScope(event.target.value as 'study' | 'site')}><option value="study">Study-wide</option><option value="site">Specific site</option></select></Field>{scope === 'site' && <Field label="Site"><select required value={siteId} onChange={(event) => setSiteId(event.target.value)} disabled={sites.length === 0}><option value="">{sites.length === 0 ? 'No active sites available' : 'Select a site'}</option>{sites.map((site) => <option key={site.id} value={site.id}>{site.name}{site.city ? ` · ${site.city}` : ''}{site.country ? ` · ${site.country}` : ''}</option>)}</select></Field>}</div>
      <div className="role-selector"><span className="eyebrow">Available {scope} roles</span>{scopedRoles.map((role) => <label key={role.id}><input type="checkbox" checked={selected.includes(role.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, role.id] : selected.filter((id) => id !== role.id))} /><span><strong>{role.name}</strong><small>{role.description || `${role.scope_level}-scope access`}</small></span></label>)}{scopedRoles.length === 0 && <Empty text={`No ${scope}-scope roles registered for ${productLabel}`} />}</div>
      <button className="primary" disabled={busy || !userId || !studyId || selected.length === 0 || (scope === 'site' && !siteId)}><UserPlus />{busy ? 'Saving…' : 'Save membership'}</button>
    </form>
    {loading ? <Spinner /> : <div className="table-wrap membership-table"><table><thead><tr><th>User</th><th>Role</th><th>Scope</th><th /></tr></thead><tbody>{visibleItems.map((item) => <tr key={item.id}><td><strong>{item.user_display_name || item.user_email || item.user_id}</strong><span>{item.user_email || item.user_id}</span></td><td><strong>{item.role_name}</strong><span>{item.role_id}</span></td><td>{item.site_id ? <><strong>Site</strong><span>{siteNames.get(item.site_id) || 'Unavailable site'}</span></> : 'Study-wide'}</td><td><button className="danger-link" onClick={() => void remove(item)}>Remove scope</button></td></tr>)}</tbody></table>{visibleItems.length === 0 && <Empty text={`No ${productLabel} assignments for this study`} />}</div>}
  </Panel><Callout>Platform owns role composition. System roles are assigned under Product access; study and site roles are assigned here.</Callout></>
}

function Users({ notify }: { notify: Notify }) {
  const [users, setUsers] = useState<User[]>([])
  const [assignments, setAssignments] = useState<Record<string, SystemAssignment[]>>({})
  const [selectedId, setSelectedId] = useState('')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async (preferredUserId?: string) => {
    setLoading(true)
    try {
      const nextUsers = await api.users()
      const assignmentEntries = await Promise.all(nextUsers.map(async (user) =>
        [user.id, await api.systemAssignments(user.id)] as const))
      setUsers(nextUsers)
      setAssignments(Object.fromEntries(assignmentEntries))
      setSelectedId((current) => {
        const preferred = preferredUserId || current
        return nextUsers.some((user) => user.id === preferred) ? preferred : nextUsers[0]?.id || ''
      })
    } catch (error) {
      notify('error', message(error))
    } finally {
      setLoading(false)
    }
  }, [notify])

  useEffect(() => { void refresh() }, [refresh])

  const create = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = event.currentTarget
    const data = new FormData(form)
    setBusy(true)
    try {
      const user = await api.createUser({
        issuer: String(data.get('issuer')),
        subject: String(data.get('subject')),
        email: String(data.get('email')),
        display_name: String(data.get('name')),
      })
      form.reset()
      setCreating(false)
      await refresh(user.id)
      notify('ok', 'Identity registered')
    } catch (error) {
      notify('error', message(error))
    } finally {
      setBusy(false)
    }
  }

  const selectedUser = users.find((user) => user.id === selectedId) || null
  const update = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!selectedUser) return
    const data = new FormData(event.currentTarget)
    setBusy(true)
    try {
      const updated = await api.patchUser(selectedUser.id, selectedUser.revision, {
        email: String(data.get('email')),
        display_name: String(data.get('name')),
        status: String(data.get('status')),
      })
      setUsers((current) => current.map((user) => user.id === updated.id ? updated : user))
      notify('ok', `${updated.display_name || updated.email || 'Account'} updated`)
    } catch (error) {
      notify('error', message(error))
    } finally {
      setBusy(false)
    }
  }

  const filtered = users.filter((user) => {
    const roles = (assignments[user.id] || []).map((item) => `${item.product} ${item.role_name}`).join(' ')
    return `${user.display_name || ''} ${user.email || ''} ${user.issuer} ${user.subject} ${user.id} ${roles}`.toLowerCase().includes(query.toLowerCase())
  })

  return <>
    <Panel title="User directory" subtitle="Every human and service identity registered in Aurinara Platform" action={<button className="primary" onClick={() => setCreating((value) => !value)}><UserPlus />{creating ? 'Close registration' : 'Register identity'}</button>}>
      <div className="toolbar"><div className="search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search users, IDs, or roles" /></div><span>{filtered.length} of {users.length} users</span></div>
      {loading ? <Spinner /> : <div className="table-wrap"><table><thead><tr><th>User</th><th>External identity</th><th>Global user ID</th><th>Product roles</th><th>Status</th><th>Revision</th><th /></tr></thead><tbody>{filtered.map((user) => <tr key={user.id}><td><strong>{user.display_name || 'Unnamed identity'}</strong><span>{user.email || 'No email address'}</span></td><td><strong>{user.issuer}</strong><span className="mono">{user.subject}</span></td><td><code>{user.id}</code></td><td><div className="role-list">{(assignments[user.id] || []).map((item) => <span className="role-pill" key={item.id}>{item.product} · {item.role_name}</span>)}{(assignments[user.id] || []).length === 0 && <span>None assigned</span>}</div></td><td><Status value={user.status} /></td><td>v{user.revision}</td><td><button className="ghost" onClick={() => setSelectedId(user.id)}>Manage</button></td></tr>)}</tbody></table>{filtered.length === 0 && <Empty text={users.length ? 'No users match this search' : 'No registered users'} />}</div>}
    </Panel>
    <div className="two-column">
      <Panel title="User details" subtitle="Update the selected global user profile and account status">
        {selectedUser ? <form key={`${selectedUser.id}-${selectedUser.revision}`} className="stack-form" onSubmit={(event) => void update(event)}>
          <Field label="Selected user"><select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>{users.map((user) => <option value={user.id} key={user.id}>{user.display_name || user.email || user.subject}</option>)}</select></Field>
          <Field label="Display name"><input name="name" required defaultValue={selectedUser.display_name || ''} /></Field>
          <Field label="Email"><input name="email" required type="email" defaultValue={selectedUser.email || ''} /></Field>
          <Field label="Account status"><select name="status" defaultValue={selectedUser.status}><option value="active">Active</option><option value="inactive">Inactive</option></select></Field>
          <div className="identity-summary"><Detail label="OIDC issuer" value={selectedUser.issuer} mono /><Detail label="Immutable subject" value={selectedUser.subject} mono /><Detail label="Global user ID" value={selectedUser.id} mono /></div>
          <button className="secondary" disabled={busy}>{busy ? 'Saving…' : 'Save user details'}</button>
        </form> : <Empty text="Select a user to manage" />}
      </Panel>
      <Panel title={creating ? 'Register an external identity' : 'What registration does'} subtitle="Connect an existing sign-in identity to the Platform">
        <Callout>Registration creates the Platform mapping used for authorization. It does not create a Cognito account, set a password, or send an invitation.</Callout>
        {creating ? <form className="stack-form registration-form" onSubmit={(event) => void create(event)}><Field label="OIDC issuer"><input name="issuer" required placeholder="https://cognito-idp.region.amazonaws.com/pool-id" /></Field><Field label="Immutable subject"><input name="subject" required placeholder="Identity provider subject (sub)" /></Field><Field label="Email"><input name="email" required type="email" /></Field><Field label="Display name"><input name="name" required /></Field><button disabled={busy} className="primary"><UserPlus />{busy ? 'Registering…' : 'Register identity'}</button></form> : <div className="registration-help"><p>Use this only after the person or service already exists in the configured identity provider.</p><p>The issuer identifies that provider, and the subject is its permanent unique ID. Together they bind future login tokens to this global user.</p><p>Assign product roles from <strong>Product access</strong> after registration.</p></div>}
      </Panel>
    </div>
  </>
}

function Roles({ notify }: { notify: Notify }) {
  const product = 'aurinara-edc'
  const [roles, setRoles] = useState<Role[]>([])
  const [permissions, setPermissions] = useState<Permission[]>([])
  const [changes, setChanges] = useState<RoleChange[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [creating, setCreating] = useState(false)
  const [scope, setScope] = useState<'system' | 'study' | 'site'>('study')
  const [selectedPermissions, setSelectedPermissions] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  const selectedRole = roles.find((role) => role.id === selectedId) || null
  const availablePermissions = permissions.filter((permission) =>
    permission.status === 'active' && permission.allowed_scope_levels.includes(scope))
  const modules = [...new Set(availablePermissions.map((permission) => permission.module || 'shared'))].sort()

  const refresh = useCallback(async (preferredRoleId?: string) => {
    try {
      const [nextRoles, nextPermissions] = await Promise.all([
        api.roles(product),
        api.permissions(product),
      ])
      setRoles(nextRoles)
      setPermissions(nextPermissions)
      setSelectedId((current) => preferredRoleId || current || nextRoles[0]?.id || '')
    } catch (error) {
      notify('error', message(error))
    }
  }, [notify])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (creating) return
    if (!selectedRole) {
      setChanges([])
      return
    }
    setScope(selectedRole.scope_level)
    setSelectedPermissions(selectedRole.permission_codes)
    void api.roleChanges(selectedRole.id).then(setChanges).catch((error) => notify('error', message(error)))
  }, [creating, notify, selectedRole])

  const startCreate = () => {
    setCreating(true)
    setScope('study')
    setSelectedPermissions([])
    setChanges([])
  }

  const togglePermission = (code: string, checked: boolean) => {
    setSelectedPermissions((current) => checked
      ? [...new Set([...current, code])]
      : current.filter((item) => item !== code))
  }

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setBusy(true)
    try {
      const common = {
        name: String(data.get('name')),
        description: String(data.get('description')) || undefined,
        scope_level: scope,
        permission_codes: selectedPermissions,
        reason: String(data.get('reason')),
      }
      const saved = creating
        ? await api.createRole(product, common)
        : await api.patchRole(selectedRole!.id, selectedRole!.revision, {
            ...common,
            description: common.description || null,
            status: String(data.get('status')),
          })
      setCreating(false)
      await refresh(saved.id)
      notify('ok', `${saved.name} saved at policy revision ${saved.revision}`)
    } catch (error) {
      notify('error', message(error))
    } finally {
      setBusy(false)
    }
  }

  const formRole = creating ? null : selectedRole
  return <>
    <div className="two-column">
      <Panel title="Platform-managed roles" subtitle="Role composition is authoritative in the control plane" action={<button className="primary" onClick={startCreate}><Plus />New role</button>}>
        <div className="role-selector">
          {roles.map((role) => <label key={role.id} className={selectedId === role.id && !creating ? 'selected' : ''}>
            <input type="radio" name="selected-role" checked={selectedId === role.id && !creating} onChange={() => { setCreating(false); setSelectedId(role.id) }} />
            <span><strong>{role.name}</strong><small>{role.scope_level} · {role.permission_codes.length} permissions · {role.status}</small></span>
          </label>)}
          {roles.length === 0 && <Empty text="No roles configured for this product" />}
        </div>
      </Panel>
      <Panel title={creating ? 'Create role' : 'Edit role'} subtitle="Every change requires a reason and creates immutable policy evidence">
        {(creating || formRole) ? <form key={creating ? 'new-role' : `${formRole!.id}-${formRole!.revision}`} className="stack-form" onSubmit={(event) => void save(event)}>
          <Field label="Role name"><input name="name" required defaultValue={formRole?.name || ''} /></Field>
          <Field label="Description"><textarea name="description" defaultValue={formRole?.description || ''} /></Field>
          <Field label="Scope"><select value={scope} onChange={(event) => { setScope(event.target.value as typeof scope); setSelectedPermissions([]) }}><option value="system">System</option><option value="study">Study</option><option value="site">Site</option></select></Field>
          {!creating && <Field label="Status"><select name="status" defaultValue={formRole?.status}><option value="active">Active</option><option value="inactive">Inactive</option></select></Field>}
          <div className="stack-form">
            <span className="eyebrow">Permissions</span>
            {modules.map((module) => <div key={module} className="role-list"><strong>{module}</strong>{availablePermissions.filter((permission) => (permission.module || 'shared') === module).map((permission) => <label key={permission.code} className="field"><span><input type="checkbox" checked={selectedPermissions.includes(permission.code)} onChange={(event) => togglePermission(permission.code, event.target.checked)} /> {permission.code}</span><small>{permission.description}</small></label>)}</div>)}
          </div>
          <Field label="Reason for change"><textarea name="reason" required minLength={3} placeholder="Explain why this access policy is needed" /></Field>
          <button className="primary" disabled={busy || selectedPermissions.length === 0}><ShieldCheck />{busy ? 'Saving…' : 'Save role policy'}</button>
        </form> : <Empty text="Select a role or create a new one" />}
      </Panel>
    </div>
    {!creating && selectedRole && <Panel title="Role change history" subtitle="Append-only evidence tied to Platform policy versions">
      <div className="table-wrap"><table><thead><tr><th>When</th><th>Action</th><th>Reason</th><th>Policy</th><th>Actor</th></tr></thead><tbody>{changes.map((change) => <tr key={change.id}><td>{new Date(change.created_at).toLocaleString()}</td><td><Status value={change.action} /></td><td>{change.reason}</td><td>v{change.policy_version}</td><td><code>{change.actor_id}</code></td></tr>)}</tbody></table>{changes.length === 0 && <Empty text="No centrally managed changes recorded yet" />}</div>
    </Panel>}
  </>
}

function ProductAccess({ notify }: { notify: Notify }) {
  const [users, setUsers] = useState<User[]>([])
  const [roles, setRoles] = useState<Role[]>([])
  const [userId, setUserId] = useState('')
  const [product, setProduct] = useState('aurinara-edc')
  const [selected, setSelected] = useState<string[]>([])
  const [assignments, setAssignments] = useState<SystemAssignment[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void Promise.all([api.users(), api.roles()])
      .then(([nextUsers, nextRoles]) => {
        setUsers(nextUsers)
        setRoles(nextRoles)
        setUserId((current) => current || nextUsers[0]?.id || '')
      })
      .catch((error) => notify('error', message(error)))
  }, [notify])

  const refresh = useCallback(async () => {
    if (!userId) return
    try {
      const next = await api.systemAssignments(userId)
      setAssignments(next)
    } catch (error) {
      notify('error', message(error))
    }
  }, [notify, userId])

  useEffect(() => { void refresh() }, [refresh])

  useEffect(() => {
    setSelected(assignments.filter((item) => item.product === product).map((item) => item.role_id))
  }, [assignments, product])

  const save = async () => {
    if (!userId) return
    setBusy(true)
    try {
      await api.replaceSystemAssignments(userId, product, selected)
      await refresh()
      notify('ok', selected.length ? `${product} access updated` : `${product} access revoked`)
    } catch (error) {
      notify('error', message(error))
    } finally {
      setBusy(false)
    }
  }

  const products = [...new Set(roles.filter((role) => role.product !== 'platform' && role.assignable && role.scope_level === 'system').map((role) => role.product))]
  const productRoles = roles.filter((role) => role.product === product && role.assignable && role.scope_level === 'system')

  return <div className="two-column">
    <Panel title="Product access" subtitle="Grant product access by assigning a system-scoped role">
      <div className="stack-form">
        <Field label="User"><select value={userId} onChange={(event) => setUserId(event.target.value)}>{users.map((item) => <option key={item.id} value={item.id}>{item.display_name || item.email || item.subject}</option>)}</select></Field>
        <Field label="Product"><select value={product} onChange={(event) => setProduct(event.target.value)}>{products.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <div className="stack-form">{productRoles.map((role) => <label key={role.id} className="field"><span><input type="checkbox" checked={selected.includes(role.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, role.id] : selected.filter((id) => id !== role.id))} /> {role.name}</span><small>{role.description}</small></label>)}</div>
        {productRoles.length === 0 && <Empty text="No assignable roles for this product" />}
        <button className="primary" disabled={busy || !userId} onClick={() => void save()}><ShieldCheck />{busy ? 'Saving…' : 'Save product access'}</button>
      </div>
    </Panel>
    <Panel title="Effective product roles" subtitle="System scope applies across the selected product">
      <div className="table-wrap"><table><thead><tr><th>Product</th><th>Role</th><th>Assigned</th></tr></thead><tbody>{assignments.map((item) => <tr key={item.id}><td>{item.product}</td><td><strong>{item.role_name}</strong></td><td>{new Date(item.assigned_at).toLocaleString()}</td></tr>)}</tbody></table>{assignments.length === 0 && <Empty text="No product roles assigned" />}</div>
      <Callout>Saving publishes a revocation-aware assignment event. Product APIs still ask Platform for the authoritative decision on every uncached permission check.</Callout>
    </Panel>
  </div>
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
