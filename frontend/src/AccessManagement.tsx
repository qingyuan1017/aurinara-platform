import { FormEvent, useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { api } from './api'
import { isUserFacingRole } from './roleVisibility'
import { isUserFacingUser } from './userVisibility'
import type { Membership, Role, Site, Study, SystemAssignment, User } from './types'

type Product = 'platform' | 'aurinara' | 'aurinara-edc'
type Scope = 'study' | 'site' | 'system'
type Notify = (kind: 'ok' | 'error', text: string) => void

const products: { id: Product; label: string }[] = [
  { id: 'platform', label: 'Platform' },
  { id: 'aurinara', label: 'Aurinara' },
  { id: 'aurinara-edc', label: 'Aurinara EDC' },
]

export function AccessManagement({ studies, notify }: { studies: Study[]; notify: Notify }) {
  const [users, setUsers] = useState<User[]>([])
  const [roles, setRoles] = useState<Role[]>([])
  const [userId, setUserId] = useState('')
  const [scope, setScope] = useState<Scope>('study')
  const [product, setProduct] = useState<Product>('aurinara')
  const [studyId, setStudyId] = useState(studies[0]?.id || '')
  const [siteId, setSiteId] = useState('')
  const [sites, setSites] = useState<Site[]>([])
  const [memberships, setMemberships] = useState<Membership[]>([])
  const [systemAssignments, setSystemAssignments] = useState<SystemAssignment[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let active = true
    void Promise.all([api.users(), api.roles()]).then(([nextUsers, nextRoles]) => {
      if (!active) return
      const people = nextUsers.filter(isUserFacingUser)
      setUsers(people)
      setRoles(nextRoles.filter(isUserFacingRole))
      setUserId((current) => people.some((user) => user.id === current) ? current : people[0]?.id || '')
      if (!people.length) setLoading(false)
    }).catch((error) => {
      if (active) { setLoading(false); notify('error', String(error)) }
    })
    return () => { active = false }
  }, [notify])

  useEffect(() => {
    if (!userId) return
    let active = true
    setLoading(true)
    setSystemAssignments([])
    setMemberships([])
    setSelected([])
    void Promise.all([
      api.systemAssignments(userId),
      studyId ? api.memberships(studyId) : Promise.resolve([]),
      studyId ? api.sites(studyId) : Promise.resolve([]),
    ]).then(([nextSystem, nextMemberships, nextSites]) => {
      if (!active) return
      setSystemAssignments(nextSystem)
      setMemberships(nextMemberships.filter((item) => item.user_id === userId))
      setSites(nextSites)
      setSiteId((current) => nextSites.some((site) => site.id === current) ? current : nextSites[0]?.id || '')
      setLoading(false)
    }).catch((error) => {
      if (active) { setLoading(false); notify('error', String(error)) }
    })
    return () => { active = false }
  }, [userId, studyId, reload, notify])

  const currentRoles = scope === 'system'
    ? systemAssignments.filter((item) => item.product === product && roles.some((role) => role.id === item.role_id && role.assignable)).map((item) => item.role_id)
    : memberships.filter((item) => item.product === product && item.site_id === (scope === 'site' ? siteId : null)).map((item) => item.role_id)
  const currentRoleKey = [...currentRoles].sort().join(',')
  useEffect(() => { setSelected(currentRoles) }, [userId, product, scope, studyId, siteId, currentRoleKey])

  const availableRoles = roles.filter((role) => role.product === product && role.scope_level === scope && role.assignable && role.status === 'active')
  const selectedUser = users.find((user) => user.id === userId)
  const selectedStudy = studies.find((study) => study.id === studyId)
  const productLabel = products.find((item) => item.id === product)?.label || product
  const changed = [...selected].sort().join(',') !== currentRoleKey
  const siteNames = new Map(sites.map((site) => [site.id, site.name]))
  const visibleSystemAssignments = systemAssignments.filter((item) => roles.some((role) => role.id === item.role_id && role.assignable))
  const readyToSave = !busy && !loading && !!userId && changed && (scope === 'system' || (!!studyId && (scope === 'study' || !!siteId)))

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!readyToSave) return
    setBusy(true)
    try {
      if (scope === 'system') {
        await api.replaceSystemAssignments(userId, product, selected)
      } else if (selected.length) {
        await api.replaceMembership(studyId, userId, product, selected, scope === 'site' ? siteId : '')
      } else {
        await api.removeMembership(studyId, userId, product, scope === 'site' ? siteId : '')
      }
      setReload((value) => value + 1)
      notify('ok', `${productLabel} access updated for ${selectedUser?.display_name || selectedUser?.email || 'user'}`)
    } catch (error) {
      notify('error', String(error))
    } finally {
      setBusy(false)
    }
  }

  return <div className="access-layout">
    <section className="access-intro"><span className="eyebrow">Role assignment</span><h2>Give someone the access they need</h2><p>Select a person, choose where access applies, then choose their roles. Existing roles for the selected scope are preselected.</p></section>
    <div className="access-grid">
      <form className="access-editor panel" onSubmit={(event) => void save(event)}>
        <div className="access-step"><span className="access-number">1</span><div><h3>Person</h3><p>Who needs access?</p></div></div>
        <label className="field"><span>User</span><select value={userId} onChange={(event) => setUserId(event.target.value)} disabled={busy || users.length === 0}><option value="">Select a user</option>{users.map((user) => <option key={user.id} value={user.id}>{user.display_name || user.email || user.subject}{user.email && user.display_name ? ` · ${user.email}` : ''}</option>)}</select></label>
        {!users.length && !loading && <p className="access-hint">No people are registered yet. Add them from the Users page.</p>}

        <div className="access-step"><span className="access-number">2</span><div><h3>Where it applies</h3><p>Choose one scope for this assignment.</p></div></div>
        <div className="access-scope-options" role="group" aria-label="Access scope">
          {([['study', 'One study', 'Roles such as SDTM Programmer'], ['site', 'One site', 'Site-specific roles'], ['system', 'All studies', 'System-wide roles']] as const).map(([value, title, description]) =>
            <button key={value} type="button" className={scope === value ? 'access-scope active' : 'access-scope'} aria-pressed={scope === value} onClick={() => setScope(value)}><strong>{title}</strong><small>{description}</small></button>)}
        </div>
        <div className="access-fields">
          <label className="field"><span>Product</span><select value={product} onChange={(event) => setProduct(event.target.value as Product)}>{products.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
          {scope !== 'system' && <label className="field"><span>Study</span><select value={studyId} onChange={(event) => setStudyId(event.target.value)} disabled={!studies.length}><option value="">Select a study</option>{studies.map((study) => <option key={study.id} value={study.id}>{study.code} · {study.display_name}</option>)}</select></label>}
          {scope === 'site' && <label className="field"><span>Site</span><select value={siteId} onChange={(event) => setSiteId(event.target.value)} disabled={loading || !sites.length}><option value="">Select a site</option>{sites.map((site) => <option key={site.id} value={site.id}>{site.name}{site.city ? ` · ${site.city}` : ''}</option>)}</select></label>}
        </div>

        <div className="access-step"><span className="access-number">3</span><div><h3>Roles</h3><p>Choose the roles to keep for this person and scope.</p></div></div>
        {loading ? <div className="access-loading">Loading current access…</div> : <div className="access-role-list">
          {availableRoles.map((role) => <label key={role.id} className={selected.includes(role.id) ? 'access-role selected' : 'access-role'}><input type="checkbox" checked={selected.includes(role.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, role.id] : current.filter((id) => id !== role.id))} /><span><strong>{role.name}</strong><small>{role.description || `${role.permission_codes.length} permissions`}</small></span></label>)}
          {!availableRoles.length && <p className="access-hint">No {productLabel} roles are available at this scope. Choose another scope or product.</p>}
        </div>}
        <div className="access-save"><span>{changed ? `${selected.length} role${selected.length === 1 ? '' : 's'} selected` : 'No unsaved changes'}</span><button className="primary" disabled={!readyToSave}><ShieldCheck />{busy ? 'Saving…' : selected.length ? 'Save access' : 'Remove access'}</button></div>
      </form>

      <aside className="access-summary panel"><header><div><h2>Current access</h2><p>{selectedUser ? selectedUser.display_name || selectedUser.email || selectedUser.subject : 'Select a person'}</p></div></header><div className="panel-body">
        {loading ? <p className="access-hint">Loading assignments…</p> : <>
          <div className="access-summary-group"><h3>All studies</h3>{visibleSystemAssignments.map((item) => <div className="access-summary-row" key={item.id}><strong>{item.role_name}</strong><span>{products.find((entry) => entry.id === item.product)?.label || item.product}</span></div>)}{!visibleSystemAssignments.length && <p>No system-wide roles</p>}</div>
          {studyId && <div className="access-summary-group"><h3>{selectedStudy?.code || 'Selected study'}</h3>{memberships.map((item) => <div className="access-summary-row" key={item.id}><strong>{item.role_name}</strong><span>{products.find((entry) => entry.id === item.product)?.label || item.product} · {item.site_id ? siteNames.get(item.site_id) || 'Site' : 'Study-wide'}</span></div>)}{!memberships.length && <p>No roles in this study</p>}</div>}
        </>}
        <p className="access-summary-note">To review another study, choose it in the form.</p>
      </div></aside>
    </div>
  </div>
}
