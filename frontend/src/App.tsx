import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, Archive, Boxes, Check, ChevronDown, ChevronRight, CircleUserRound, Database, FolderKanban,
  Bot, Gauge, KeyRound, LogOut, Menu, ChevronLeft, Plus, RefreshCw, Search, Settings2,
  ShieldCheck, SquareTerminal, X, XCircle,
} from 'lucide-react'
import { api, ApiError } from './api'
import { claims, clearToken, completeLogin, getToken, isOidcConfigured, startLogin } from './auth'
import { isCognitoConfigured, restoreCognitoSession, signInWithPassword, signOutCognito } from './cognito'
import { isUserFacingRole } from './roleVisibility'
import { isUserFacingUser } from './userVisibility'
import type { Permission, Role, RoleChange, Study, SystemAssignment, User } from './types'
import { RepositoryPage } from './RepositoryPage'
import { AccessManagement } from './AccessManagement'
import { ThemeToggle } from './theme'
import { PlaygroundPage } from './PlaygroundPage'
import { AgentCatalogPage } from './AgentCatalogPage'
import { WorkspacePage } from './WorkspacePage'

type Page = 'overview' | 'repository' | 'workspace' | 'playground' | 'agent-catalog' | 'studies' | 'roles' | 'access' | 'users'

const nav: { id: Page; label: string; icon: typeof Gauge }[] = [
  { id: 'overview', label: 'Overview', icon: Gauge },
  { id: 'repository', label: 'Repository', icon: FolderKanban },
  { id: 'workspace', label: 'Workspace', icon: SquareTerminal },
  { id: 'playground', label: 'Playground', icon: Bot },
  { id: 'agent-catalog', label: 'Agent catalog', icon: Settings2 },
  { id: 'studies', label: 'Study registry', icon: Database },
  { id: 'users', label: 'Users', icon: CircleUserRound },
  { id: 'roles', label: 'Roles & permissions', icon: ShieldCheck },
  { id: 'access', label: 'Access management', icon: ShieldCheck },
]

function pageFromHash(): Page {
  const requested = location.hash.slice(1)
  if (requested === 'memberships') return 'access'
  return nav.some((item) => item.id === requested) ? requested as Page : 'overview'
}

function message(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : 'Unexpected error'
}

export function App() {
  const [ready, setReady] = useState(false)
  const [user, setUser] = useState<User | null>(null)
  const [studies, setStudies] = useState<Study[]>([])
  const [page, setPage] = useState<Page>(pageFromHash)
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

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
          setPage(pageFromHash())
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
    <div className={sidebarCollapsed ? 'app-shell sidebar-collapsed' : 'app-shell'}>
      <aside className={menuOpen ? 'sidebar open' : 'sidebar'}>
        <div className="brand">
          <button className="sidebar-brand-expand" aria-label="Expand sidebar" title="Expand sidebar" aria-expanded={false} onClick={() => setSidebarCollapsed(false)}><span className="brand-mark"><ShieldCheck /></span></button>
          <div className="brand-mark brand-mark-expanded"><ShieldCheck /></div>
          <div className="brand-copy"><strong>Aurinara</strong><span>Platform control plane</span></div>
          <button className="sidebar-toggle" aria-label="Collapse sidebar" title="Collapse sidebar" aria-expanded onClick={() => setSidebarCollapsed(true)}><ChevronLeft /></button>
        </div>
        <nav aria-label="Primary navigation">
          {nav.map((item) => <button key={item.id} title={sidebarCollapsed ? item.label : undefined} aria-label={sidebarCollapsed ? item.label : undefined} className={page === item.id ? 'nav-item active' : 'nav-item'} onClick={() => selectPage(item.id)}><item.icon /><span>{item.label}</span><ChevronRight /></button>)}
        </nav>
        <div className="sidebar-foot">
          <span className="eyebrow">Signed in as</span><strong>{user.display_name || user.email || user.subject}</strong>
          <span className="muted truncate">{user.email || user.issuer}</span>
          <button className="ghost wide" onClick={() => { signOutCognito(); setUser(null) }}><LogOut />Sign out</button>
        </div>
      </aside>
      {menuOpen && <button className="scrim" aria-label="Close menu" onClick={() => setMenuOpen(false)} />}
      <main>
        <header className="topbar"><button className="icon mobile-only" onClick={() => setMenuOpen(true)}><Menu /></button><div className="topbar-title"><span className="eyebrow">Platform administration</span><h1>{title}</h1></div><div className="topbar-actions"><ThemeToggle /><AccountMenu user={user} onSignOut={() => { signOutCognito(); setUser(null) }} /></div></header>
        <div className="content">
          {page === 'overview' && <Overview user={user} studies={studies} onNavigate={selectPage} />}
          {page === 'repository' && <RepositoryPage studies={studies} notify={notify} />}
          {page === 'workspace' && <WorkspacePage />}
          {page === 'playground' && <PlaygroundPage />}
          {page === 'agent-catalog' && <AgentCatalogPage />}
          {page === 'studies' && <Studies studies={studies} setStudies={setStudies} notify={notify} />}
          {page === 'roles' && <Roles notify={notify} />}
          {page === 'access' && <AccessManagement studies={studies} notify={notify} />}
          {page === 'users' && <Users notify={notify} />}
        </div>
      </main>
      {notice && <div role="status" className={`toast ${notice.kind}`} >{notice.kind === 'ok' ? <Check /> : <XCircle />}<span>{notice.text}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    </div>
  )
}

function AccountMenu({ user, onSignOut }: { user: User; onSignOut: () => void }) {
  const primary = user.display_name || user.email || 'Account'
  const secondary = user.email || 'Signed in'
  const initials = primary.trim().split(/\s+/).filter(Boolean)
  const avatar = (initials.length > 1 ? `${initials[0][0]}${initials.at(-1)?.[0] ?? ''}` : primary.slice(0, 2)).toUpperCase()
  return <details className="account-menu">
    <summary className="account-trigger" aria-label={`Open user menu for ${primary}`}>
      <span className="account-avatar" aria-hidden="true">{avatar}</span>
      <span className="account-copy"><strong>{primary}</strong><span>{secondary}</span></span>
      <ChevronDown className="account-chevron" aria-hidden="true" />
    </summary>
    <div className="account-popover" role="menu">
      <div className="account-identity"><strong>{primary}</strong><span>{secondary}</span></div>
      <div className="account-separator" role="separator" />
      <button role="menuitem" className="account-signout" onClick={onSignOut}><LogOut />Sign out</button>
    </div>
  </details>
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
  return <div className="login-page"><div className="login-orbit orbit-one"/><div className="login-orbit orbit-two"/><div className="login-stack"><div className="login-brand"><div className="brand-mark"><ShieldCheck /></div><div><strong>Aurinara</strong><span>Platform control plane</span></div></div><section className="login-card">
    <h1>Platform Console</h1>
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
  </section><footer>Private administrative surface · Access is audited and policy controlled</footer></div></div>
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
    <div className="table-wrap"><table><thead><tr><th>Study</th><th>Sponsor</th><th>Status</th><th>Revision</th><th /></tr></thead><tbody>{filtered.map((study) => <tr key={study.id}><td><strong>{study.display_name}</strong><span>{study.code}</span></td><td>{study.sponsor ?? 'Not supplied'}</td><td><Status value={study.status} /></td><td>v{study.revision}</td><td><button className="ghost" onClick={() => void toggle(study)}><Archive />{study.status === 'active' ? 'Archive' : 'Activate'}</button></td></tr>)}</tbody></table></div>
  </Panel>
}

function Users({ notify }: { notify: Notify }) {
  const [users, setUsers] = useState<User[]>([])
  const [assignments, setAssignments] = useState<Record<string, SystemAssignment[]>>({})
  const [selectedId, setSelectedId] = useState('')
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async (preferredUserId?: string) => {
    setLoading(true)
    try {
      const nextUsers = (await api.users()).filter(isUserFacingUser)
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
    return `${user.display_name || ''} ${user.email || ''} ${roles}`.toLowerCase().includes(query.toLowerCase())
  })

  return <>
    <Panel title="User directory" subtitle="People and service accounts provisioned for Aurinara Platform">
      <div className="toolbar"><div className="search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by name, email, or role" /></div><span>{filtered.length} of {users.length} users</span></div>
      {loading ? <Spinner /> : <div className="table-wrap"><table><thead><tr><th>User</th><th>Product roles</th><th>Status</th><th>Revision</th><th /></tr></thead><tbody>{filtered.map((user) => <tr key={user.id}><td><strong>{user.display_name || 'Unnamed account'}</strong><span>{user.email || 'No email address'}</span></td><td><div className="role-list">{(assignments[user.id] || []).map((item) => <span className="role-pill" key={item.id}>{item.product} · {item.role_name}</span>)}{(assignments[user.id] || []).length === 0 && <span>None assigned</span>}</div></td><td><Status value={user.status} /></td><td>v{user.revision}</td><td><button className="ghost" onClick={() => setSelectedId(user.id)}>Manage</button></td></tr>)}</tbody></table>{filtered.length === 0 && <Empty text={users.length ? 'No users match this search' : 'No provisioned users'} />}</div>}
    </Panel>
    <div className="two-column">
      <Panel title="User details" subtitle="Update the selected global user profile and account status">
        {selectedUser ? <form key={`${selectedUser.id}-${selectedUser.revision}`} className="stack-form" onSubmit={(event) => void update(event)}>
          <Field label="Selected user"><select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>{users.map((user) => <option value={user.id} key={user.id}>{user.display_name || user.email || user.subject}</option>)}</select></Field>
          <Field label="Display name"><input name="name" required defaultValue={selectedUser.display_name || ''} /></Field>
          <Field label="Email"><input name="email" required type="email" defaultValue={selectedUser.email || ''} /></Field>
          <Field label="Account status"><select name="status" defaultValue={selectedUser.status}><option value="active">Active</option><option value="inactive">Inactive</option></select></Field>
          <details className="technical-details"><summary>Technical details</summary><div className="identity-summary"><Detail label="Identity provider" value={selectedUser.issuer} mono /><Detail label="Provider subject" value={selectedUser.subject} mono /><Detail label="Internal user reference" value={selectedUser.id} mono /></div></details>
          <button className="secondary" disabled={busy}>{busy ? 'Saving…' : 'Save user details'}</button>
        </form> : <Empty text="Select a user to manage" />}
      </Panel>
      <Panel title="How users appear here" subtitle="Identity provisioning stays outside the day-to-day access workflow">
        <Callout>Users appear after secure sign-in or approved identity automation. Administrators never need to copy identity-provider identifiers.</Callout>
        <div className="registration-help"><p>If a person is missing, ask them to sign in through the configured provider or use the organization&apos;s provisioning process.</p><p>After the account appears, assign product and study roles from <strong>Access management</strong>.</p></div>
      </Panel>
    </div>
  </>
}

function Roles({ notify }: { notify: Notify }) {
  const [product, setProduct] = useState('aurinara')
  const [roles, setRoles] = useState<Role[]>([])
  const [permissions, setPermissions] = useState<Permission[]>([])
  const [changes, setChanges] = useState<RoleChange[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [creating, setCreating] = useState(false)
  const [scope, setScope] = useState<'system' | 'study' | 'site'>('study')
  const [selectedPermissions, setSelectedPermissions] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [actorNames, setActorNames] = useState<Record<string, string>>({})

  const selectedRole = roles.find((role) => role.id === selectedId) || null
  const availablePermissions = permissions.filter((permission) =>
    permission.status === 'active' && permission.allowed_scope_levels.includes(scope))
  const modules = [...new Set(availablePermissions.map((permission) => permission.module || 'shared'))].sort()

  const refresh = useCallback(async (preferredRoleId?: string) => {
    try {
      const [nextRoles, nextPermissions, nextUsers] = await Promise.all([
        api.roles(product),
        api.permissions(product),
        api.users(),
      ])
      const userFacingRoles = nextRoles.filter(isUserFacingRole)
      setRoles(userFacingRoles)
      setPermissions(nextPermissions)
      setActorNames(Object.fromEntries(nextUsers.map((user) => [user.id, user.display_name || user.email || 'Platform user'])))
      setSelectedId(
        preferredRoleId && userFacingRoles.some((role) => role.id === preferredRoleId)
          ? preferredRoleId
          : userFacingRoles[0]?.id || '',
      )
    } catch (error) {
      notify('error', message(error))
    }
  }, [notify, product])

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
        ? await api.createRole(product, { ...common, key: String(data.get('key')) })
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
      <Panel title="Platform-managed roles" subtitle="Role composition is authoritative in the control plane" action={<div className="toolbar"><select aria-label="Role product" value={product} onChange={(event) => { setProduct(event.target.value); setRoles([]); setPermissions([]); setChanges([]); setCreating(false); setSelectedId('') }}><option value="platform">Platform</option><option value="aurinara">Aurinara</option><option value="aurinara-edc">Aurinara EDC</option></select><button className="primary" onClick={startCreate}><Plus />New role</button></div>}>
        <div className="role-selector">
          {roles.map((role) => <label key={role.id} className={selectedId === role.id && !creating ? 'selected' : ''}>
            <input type="radio" name="selected-role" checked={selectedId === role.id && !creating} onChange={() => { setCreating(false); setSelectedId(role.id) }} />
            <span><strong>{role.name}</strong><small>{role.scope_level} · {role.permission_policy} · {role.permission_codes.length} registered permissions · {role.status}</small></span>
          </label>)}
          {roles.length === 0 && <Empty text="No roles configured for this product" />}
        </div>
      </Panel>
      <Panel title={creating ? 'Create role' : 'Edit role'} subtitle="Every change requires a reason and creates immutable policy evidence">
        {formRole?.built_in ? <div className="registration-help"><p><strong>{formRole.name}</strong> is a built-in Platform role.</p><p>Its policy is managed by the Platform release and cannot be edited. Clone its explicit permissions into a custom role when a different composition is needed.</p></div> : (creating || formRole) ? <form key={creating ? 'new-role' : `${formRole!.id}-${formRole!.revision}`} className="stack-form" onSubmit={(event) => void save(event)}>
          {creating && <Field label="Stable role key"><input name="key" required pattern="[a-z][a-z0-9_.-]{2,159}" placeholder="custom.study_programmer" /></Field>}
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
      <div className="table-wrap"><table><thead><tr><th>When</th><th>Action</th><th>Reason</th><th>Policy</th><th>Actor</th></tr></thead><tbody>{changes.map((change) => <tr key={change.id}><td>{new Date(change.created_at).toLocaleString()}</td><td><Status value={change.action} /></td><td>{change.reason}</td><td>v{change.policy_version}</td><td>{actorNames[change.actor_id] || 'Platform administrator'}</td></tr>)}</tbody></table>{changes.length === 0 && <Empty text="No centrally managed changes recorded yet" />}</div>
    </Panel>}
  </>
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
