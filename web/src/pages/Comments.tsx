import { useEffect, useRef, useState } from 'react'
import { api, type DiffBlock, type Thread } from '../api'
import { ago, AutoTextarea, Dialog, useToast } from '../ui'
import type { Selection } from '../RichEditor'
import * as Ic from '../icons'

/** Threads whose last word is the author's: what "按批注修改" will send. */
export function waiting(threads: Thread[]): Thread[] {
  return threads.filter(t => t.status === 'open' && (t.replies.length ? t.replies[t.replies.length - 1] : t).author === 'gary')
}

/** Side panel listing comments like a Feishu doc: on a selection or on the whole piece, with replies. */
export function CommentsPanel({ draftId, threads, order, pendingSel, onClearSel, active, onActivate, onChanged, onRevise, busy }: {
  draftId: number; threads: Thread[]; order: Map<number, number | null>
  pendingSel: Selection | null; onClearSel: () => void
  active: number | null; onActivate: (id: number | null) => void
  onChanged: () => void; onRevise: () => void; busy: boolean
}) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [filter, setFilter] = useState<'open' | 'all'>('open')
  const inputRef = useRef<HTMLTextAreaElement | null>(null)
  useEffect(() => { if (pendingSel) window.setTimeout(() => inputRef.current?.focus(), 30) }, [pendingSel])
  useEffect(() => {
    if (active == null) return
    document.querySelector(`[data-thread="${active}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [active])

  async function add() {
    if (!text.trim()) return
    try {
      await api.post(`/api/drafts/${draftId}/comments`, { body: text, ...(pendingSel || {}) })
      setText(''); onClearSel(); onChanged()
    } catch (e) { toast((e as Error).message, 'error') }
  }

  const todo = waiting(threads)
  const shown = threads.filter(t => filter === 'all' || t.status === 'open')
    .sort((a, b) => (order.get(a.id) ?? 1e9) - (order.get(b.id) ?? 1e9) || a.id - b.id)

  return (
    <div className="stack comments">
      <div className="revise-bar">
        <div className="small muted">{todo.length ? `${todo.length} 条批注等 AI 处理` : '写批注、或者回复 AI 的批注，写完一起发给 AI 改'}</div>
        <button className="btn primary sm" disabled={!todo.length || busy} onClick={onRevise}>
          {busy ? <Ic.Loader size={14} /> : <Ic.Sparkle size={14} />} 按批注修改
        </button>
      </div>

      <div className="cm-compose">
        {pendingSel ? (
          <div className="cm-quote"><span>「{pendingSel.quote.slice(0, 60)}{pendingSel.quote.length > 60 ? '…' : ''}」</span>
            <button className="btn ghost sm icon" aria-label="改成对全文提意见" onClick={onClearSel}><Ic.Close size={13} /></button></div>
        ) : <div className="small faint">在正文里选中一段文字可以针对那段批注；这里不选就是对全文提意见。</div>}
        <AutoTextarea inputRef={inputRef} className="textarea" value={text} onChange={e => setText(e.target.value)}
          placeholder={pendingSel ? '对这段的意见或要求…' : '对全文的意见或要求…'}
          onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) add() }} />
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn sm" disabled={!text.trim()} onClick={add}>添加批注</button>
        </div>
      </div>

      <div className="row">
        <div className="segmented" role="group" aria-label="筛选">
          <button aria-pressed={filter === 'open'} onClick={() => setFilter('open')}>未解决 <span className="n">{threads.filter(t => t.status === 'open').length}</span></button>
          <button aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>全部 <span className="n">{threads.length}</span></button>
        </div>
      </div>

      {shown.length === 0 && <div className="small muted" style={{ padding: '8px 2px' }}>没有{filter === 'open' ? '未解决的' : ''}批注。</div>}
      {shown.map(t => <ThreadCard key={t.id} t={t} active={active === t.id} located={order.get(t.id) !== null}
        onActivate={() => onActivate(active === t.id ? null : t.id)} onChanged={onChanged} />)}
    </div>
  )
}

function ThreadCard({ t, active, located, onActivate, onChanged }: { t: Thread; active: boolean; located: boolean; onActivate: () => void; onChanged: () => void }) {
  const toast = useToast()
  const [reply, setReply] = useState('')
  const [open, setOpen] = useState(false)
  const msgs = [t, ...t.replies]
  const needsAi = waiting([t]).length > 0
  async function send() {
    if (!reply.trim()) return
    try { await api.post(`/api/comments/${t.id}/replies`, { body: reply }); setReply(''); setOpen(false); onChanged() }
    catch (e) { toast((e as Error).message, 'error') }
  }
  async function setStatus(status: 'open' | 'resolved') {
    try { await api.patch(`/api/comments/${t.id}`, { status }); onChanged() } catch (e) { toast((e as Error).message, 'error') }
  }
  async function remove() {
    if (!window.confirm('删掉这条批注和它的回复？')) return
    try { await api.del(`/api/comments/${t.id}`); onChanged() } catch (e) { toast((e as Error).message, 'error') }
  }
  return (
    <div className={'thread-card' + (active ? ' active' : '') + (t.status === 'resolved' ? ' resolved' : '')} data-thread={t.id}>
      <button className="tq" onClick={onActivate} title={t.quote ? '在正文里定位' : undefined}>
        {t.quote ? <>「{t.quote.slice(0, 48)}{t.quote.length > 48 ? '…' : ''}」{!located && <span className="pill warn small">原文已改动</span>}</> : <span className="faint">全文</span>}
        {t.status === 'resolved' && <span className="pill ok small">已解决</span>}
        {needsAi && <span className="pill accent small">等 AI</span>}
      </button>
      {msgs.map(m => (
        <div key={m.id} className={'cm-msg ' + m.author}>
          <div className="who">{m.author === 'ai' ? <><Ic.Sparkle size={12} /> AI</> : '你'}<span className="faint"> · {ago(m.created_at)}</span></div>
          <div className="txt">{m.body}</div>
        </div>
      ))}
      {open ? (
        <div className="stack" style={{ gap: 6 }}>
          <AutoTextarea className="textarea" value={reply} onChange={e => setReply(e.target.value)} placeholder="回复，比如你要补的那句话"
            onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send() }} />
          <div className="row" style={{ justifyContent: 'flex-end', gap: 6 }}>
            <button className="btn ghost sm" onClick={() => setOpen(false)}>取消</button>
            <button className="btn sm" disabled={!reply.trim()} onClick={send}>回复</button>
          </div>
        </div>
      ) : (
        <div className="cm-acts">
          <button className="btn ghost sm" onClick={() => setOpen(true)}>回复</button>
          {t.status === 'open'
            ? <button className="btn ghost sm" onClick={() => setStatus('resolved')}><Ic.Check size={13} /> 解决</button>
            : <button className="btn ghost sm" onClick={() => setStatus('open')}>重新打开</button>}
          {t.author === 'gary' && <button className="btn ghost sm danger" aria-label="删除批注" onClick={remove}><Ic.Trash size={13} /></button>}
        </div>
      )}
    </div>
  )
}

/** Before/after of one AI revision. */
export function DiffDialog({ open, draftId, before, onClose }: { open: boolean; draftId: number; before: number | null; onClose: () => void }) {
  const [blocks, setBlocks] = useState<DiffBlock[] | null>(null)
  const [all, setAll] = useState(false)
  useEffect(() => {
    if (!open || !before) return
    setBlocks(null)
    api.get<DiffBlock[]>(`/api/drafts/${draftId}/diff?a=${before}`).then(setBlocks).catch(() => setBlocks([]))
  }, [open, before, draftId])
  const changed = (blocks || []).filter(b => b.op !== 'same').length
  return (
    <Dialog open={open} onClose={onClose} title="这一轮改了什么" wide
      footer={<><label className="row small muted" style={{ marginRight: 'auto', gap: 6 }}><input type="checkbox" checked={all} onChange={e => setAll(e.target.checked)} /> 显示没改的段落</label>
        <button className="btn" onClick={onClose}>关闭</button></>}>
      {blocks === null ? <div className="skeleton" style={{ height: 80 }} /> : (
        <div className="diff">
          <div className="small muted" style={{ marginBottom: 8 }}>{changed ? `改动了 ${changed} 段。绿色是新加的，红色划线是删掉的。` : '这一轮没有改正文。'}</div>
          {blocks.map((b, i) => {
            if (b.op === 'same') return all ? <p key={i} className="d-same">{b.text}</p> : null
            if (b.op === 'change') return <p key={i} className="d-change">{b.parts.map((s, j) => s.op === 'same' ? <span key={j}>{s.text}</span> : s.op === 'ins' ? <ins key={j}>{s.text}</ins> : <del key={j}>{s.text}</del>)}</p>
            return b.op === 'ins' ? <p key={i} className="d-ins"><ins>{b.text}</ins></p> : <p key={i} className="d-del"><del>{b.text}</del></p>
          })}
        </div>
      )}
    </Dialog>
  )
}
