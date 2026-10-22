import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { Archive, Bot, Check, Plus, Send, Square, X } from 'lucide-react'
import {
  playgroundApi,
  type Conversation,
  type PlaygroundAgent,
  type StreamEvent,
} from './playground-client'

type Message = { id: string; role: 'user' | 'assistant'; text: string }
type Approval = Extract<StreamEvent, { type: 'approval_required' }>

export function PlaygroundPage() {
  const [agents, setAgents] = useState<PlaygroundAgent[]>([])
  const [agentId, setAgentId] = useState('')
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)
  const [approval, setApproval] = useState<Approval | null>(null)
  const [toolStatus, setToolStatus] = useState('')
  const [error, setError] = useState('')
  const controller = useRef<AbortController | null>(null)
  const historyRequest = useRef(0)

  const openConversation = useCallback(async (item: Conversation) => {
    const requestId = ++historyRequest.current
    setConversation(item)
    setMessages([])
    setToolStatus('')
    setApproval(item.pending_interrupt_id ? {
      type: 'approval_required', interrupt_id: item.pending_interrupt_id,
      tool_name: item.pending_tool_name || 'tool',
      summary: 'This saved run is waiting for your approval.',
    } : null)
    const history = await playgroundApi.messages(item.id)
    if (requestId === historyRequest.current) setMessages(history.filter((message) => message.role === 'user' || message.role === 'assistant').map((message) => ({ id: message.id, role: message.role as 'user' | 'assistant', text: message.content })))
  }, [])

  const loadConversations = useCallback(async (selectedAgentId: string, preferredId?: string, loadHistory = true) => {
    const items = await playgroundApi.conversations(selectedAgentId)
    setConversations(items)
    const selected = items.find((item) => item.id === preferredId) ?? items[0] ?? null
    if (selected && loadHistory) await openConversation(selected)
    else setConversation(selected)
  }, [openConversation])

  useEffect(() => {
    void playgroundApi.agents().then(async (items) => {
      setAgents(items)
      const firstId = items[0]?.id || ''
      setAgentId(firstId)
      if (firstId) await loadConversations(firstId)
    }).catch((reason) => setError(reason instanceof Error ? reason.message : 'Unable to load agents'))
    return () => controller.current?.abort()
  }, [loadConversations])

  const startNew = () => {
    historyRequest.current += 1
    controller.current?.abort()
    setConversation(null)
    setMessages([])
    setApproval(null)
    setToolStatus('')
    setError('')
    setBusy(false)
  }

  const archiveCurrent = async () => {
    if (!conversation) return
    await playgroundApi.updateConversation(conversation.id, { state: 'archived' })
    startNew()
    await loadConversations(agentId)
  }

  const applyEvent = (assistantId: string, event: StreamEvent) => {
    if (event.type === 'text_delta') setMessages((items) => items.map((item) => item.id === assistantId ? { ...item, text: item.text + event.text } : item))
    if (event.type === 'tool_status') setToolStatus(`${event.tool_name} · ${event.status}`)
    if (event.type === 'approval_required') { setApproval(event); setToolStatus(`${event.tool_name} · awaiting approval`) }
    if (event.type === 'error') setError(event.message)
  }

  const run = async (kind: 'message' | 'approval', value: string, pending?: Approval, decision?: 'approve' | 'deny') => {
    if (!agentId) return
    setBusy(true)
    setError('')
    try {
      const current = conversation || await playgroundApi.createConversation(agentId)
      if (!conversation) setConversation(current)
      const assistantId = crypto.randomUUID()
      setMessages((items) => [...items, { id: assistantId, role: 'assistant', text: '' }])
      controller.current = new AbortController()
      if (kind === 'message') await playgroundApi.message(current.id, value, controller.current.signal, (event) => applyEvent(assistantId, event))
      else if (pending && decision) await playgroundApi.approval(current.id, pending.interrupt_id, decision, controller.current.signal, (event) => applyEvent(assistantId, event))
      await loadConversations(agentId, current.id, false)
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === 'AbortError')) setError(reason instanceof Error ? reason.message : 'Agent request failed')
    } finally { setBusy(false); controller.current = null }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = prompt.trim()
    if (!value || busy || approval) return
    setPrompt('')
    setMessages((items) => [...items, { id: crypto.randomUUID(), role: 'user', text: value }])
    void run('message', value)
  }

  const decide = (decision: 'approve' | 'deny') => {
    if (!approval) return
    const pending = approval
    setApproval(null)
    void run('approval', '', pending, decision)
  }

  return <div className="playground-layout">
    <aside className="panel playground-history">
      <label className="field"><span>Agent</span><select value={agentId} onChange={(event) => { const nextId = event.target.value; startNew(); setConversations([]); setAgentId(nextId); void loadConversations(nextId).catch((reason) => setError(reason instanceof Error ? reason.message : 'Unable to load conversations')) }} disabled={busy}>{agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
      <button className="primary" onClick={startNew}><Plus />New conversation</button>
      <div className="role-selector">{conversations.map((item) => <button key={item.id} className={conversation?.id === item.id ? 'selected' : ''} onClick={() => void openConversation(item).catch((reason) => setError(reason instanceof Error ? reason.message : 'Unable to load AgentCore history'))}><span><strong>{item.title || 'New conversation'}</strong><small>{new Date(item.updated_at).toLocaleString()} · {item.state}</small></span></button>)}</div>
    </aside>
    <section className="playground-page">
      <div className="playground-toolbar"><div><strong>{conversation?.title || 'New conversation'}</strong><small>History and recall are stored in AgentCore Memory.</small></div>{conversation && <button className="secondary" onClick={() => void archiveCurrent()}><Archive />Archive</button>}</div>
      <div className="playground-chat" aria-live="polite">
        {messages.length === 0 && <div className="playground-empty"><Bot /><strong>Start a conversation</strong><span>{agents.find((item) => item.id === agentId)?.description || 'Choose an agent and ask a question.'}</span></div>}
        {messages.map((item) => <div key={item.id} className={`playground-message ${item.role}`}><small>{item.role === 'user' ? 'You' : 'Agent'}</small><p>{item.text || (busy ? 'Thinking…' : '')}</p></div>)}
        {toolStatus && <div className="playground-tool">{toolStatus}</div>}
        {approval && <div className="playground-approval"><strong>Approve this write?</strong><p>{approval.summary || `${approval.tool_name} wants to change data.`}</p>{approval.arguments !== undefined && <details><summary>Review details</summary><pre>{JSON.stringify(approval.arguments, null, 2)}</pre></details>}<div><button className="primary" onClick={() => decide('approve')}><Check />Approve once</button><button className="secondary" onClick={() => decide('deny')}><X />Deny</button></div></div>}
        {error && <div className="inline-note error">{error}</div>}
      </div>
      <form className="playground-composer" onSubmit={submit}><textarea aria-label="Message" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Ask the selected agent…" disabled={!agentId || busy || Boolean(approval)} /><div>{busy ? <button type="button" className="secondary" onClick={() => controller.current?.abort()}><Square />Stop</button> : <button className="primary" disabled={!agentId || !prompt.trim() || Boolean(approval)}><Send />Send</button>}</div></form>
    </section>
  </div>
}
