import { FormEvent, useCallback, useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { playgroundApi, type AdminAgent } from './playground-client'
import './AgentCatalogPage.css'

const products = ['platform', 'aurinara', 'aurinara-edc']

function agentBody(form: HTMLFormElement) {
  const data = new FormData(form)
  return {
    name: String(data.get('name')),
    description: String(data.get('description')) || null,
    product_codes: data.getAll('products').map(String),
    runtime_arn: String(data.get('runtime_arn')),
    memory_id: String(data.get('memory_id')) || null,
    runtime_qualifier: String(data.get('runtime_qualifier')) || 'DEFAULT',
    enabled: data.get('enabled') === 'on',
  }
}

function AgentForm({
  agent,
  error,
  saving,
  onSubmit,
  onCancel,
}: {
  agent: AdminAgent | null
  error: string
  saving: boolean
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  onCancel?: () => void
}) {
  return (
    <form key={agent?.id || 'new'} className="stack-form agent-form" onSubmit={onSubmit}>
      <label className="field">
        <span>Name</span>
        <input name="name" defaultValue={agent?.name || ''} autoFocus={!agent} required />
      </label>
      <label className="field">
        <span>Description</span>
        <textarea name="description" defaultValue={agent?.description || ''} />
      </label>
      <fieldset>
        <legend>Products</legend>
        {products.map((product) => (
          <label key={product}>
            <input
              type="checkbox"
              name="products"
              value={product}
              defaultChecked={agent?.product_codes.includes(product)}
            />{' '}
            {product}
          </label>
        ))}
      </fieldset>
      <label className="field">
        <span>AgentCore runtime ARN</span>
        <input name="runtime_arn" defaultValue={agent?.runtime_arn || ''} required />
        <small>Unique runtime binding for this catalog entry.</small>
      </label>
      <label className="field">
        <span>AgentCore memory ID (optional)</span>
        <input
          name="memory_id"
          defaultValue={agent?.memory_id || ''}
          placeholder="Auto-discover from the runtime account"
        />
      </label>
      <label className="field">
        <span>Qualifier</span>
        <input
          name="runtime_qualifier"
          defaultValue={agent?.runtime_qualifier || 'DEFAULT'}
          required
        />
      </label>
      <label className="agent-enabled">
        <input type="checkbox" name="enabled" defaultChecked={agent?.enabled} /> Enabled
      </label>
      {error && <div className="inline-note error">{error}</div>}
      <div className="agent-form-actions">
        {onCancel && (
          <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
        )}
        <button className="primary" disabled={saving}>
          {saving ? 'Saving…' : agent ? 'Save agent' : 'Create agent'}
        </button>
      </div>
    </form>
  )
}

export function AgentCatalogPage() {
  const [agents, setAgents] = useState<AdminAgent[]>([])
  const [selected, setSelected] = useState<AdminAgent | null>(null)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async (preferredId?: string) => {
    try {
      const items = await playgroundApi.adminAgents()
      setAgents(items)
      setSelected((current) => (
        items.find((item) => item.id === (preferredId || current?.id)) || items[0] || null
      ))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load catalog')
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!creating) return
    const previousOverflow = document.body.style.overflow
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setCreating(false)
    }
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [creating])

  const save = async (event: FormEvent<HTMLFormElement>, agent: AdminAgent | null) => {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      const saved = agent
        ? await playgroundApi.patchAgent(agent, agentBody(event.currentTarget))
        : await playgroundApi.createAgent(agentBody(event.currentTarget))
      await load(saved.id)
      if (!agent) setCreating(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to save agent')
    } finally {
      setSaving(false)
    }
  }

  const openCreate = () => {
    setError('')
    setCreating(true)
  }

  const closeCreate = () => {
    if (saving) return
    setCreating(false)
    setError('')
  }

  return (
    <>
      <div className="two-column">
        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Agent catalog</h2>
              <p>Runtimes available in the three Aurinara products</p>
            </div>
            <button className="primary" onClick={openCreate}><Plus />New agent</button>
          </div>
          <div className="agent-catalog-body">
            <div className="role-selector">
              {agents.map((agent) => (
                <button
                  key={agent.id}
                  className={selected?.id === agent.id ? 'selected' : ''}
                  onClick={() => { setSelected(agent); setError('') }}
                >
                  <span>
                    <strong>{agent.name}</strong>
                    <small>
                      {agent.product_codes.join(', ')} · {agent.enabled ? 'enabled' : 'disabled'}
                    </small>
                  </span>
                </button>
              ))}
              {!agents.length && <div className="empty">No agents configured.</div>}
            </div>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <div>
              <h2>Agent details</h2>
              <p>Runtime and memory details are visible only to Platform administrators</p>
            </div>
          </div>
          <div className="agent-catalog-body">
            {selected ? (
              <AgentForm
                agent={selected}
                error={error}
                saving={saving}
                onSubmit={(event) => void save(event, selected)}
              />
            ) : (
              <div className="empty">Select an agent to edit it, or create a new agent.</div>
            )}
          </div>
        </section>
      </div>

      {creating && (
        <div className="agent-drawer-layer">
          <button
            type="button"
            className="agent-drawer-backdrop"
            aria-label="Close create agent drawer"
            onClick={closeCreate}
          />
          <aside
            className="agent-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-agent-title"
          >
            <header>
              <div>
                <h2 id="create-agent-title">Create agent</h2>
                <p>Add an AgentCore runtime to the shared catalog.</p>
              </div>
              <button type="button" className="ghost icon" onClick={closeCreate} aria-label="Close">
                <X />
              </button>
            </header>
            <div className="agent-drawer-body">
              <AgentForm
                agent={null}
                error={error}
                saving={saving}
                onSubmit={(event) => void save(event, null)}
                onCancel={closeCreate}
              />
            </div>
          </aside>
        </div>
      )}
    </>
  )
}
