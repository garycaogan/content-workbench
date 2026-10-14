import { useState } from 'react'
import { api } from '../api'
import { go } from '../App'
import { ago, AutoTextarea, Empty, usePoll, useToast } from '../ui'
import * as Ic from '../icons'

type Lesson = { id: string; rule: string; examples?: { before: string; after: string }[]; evidence?: number; source?: string; enabled?: boolean }
type Round = { id: number; draft_id: number; title: string | null; trigger: string; stats: Record<string, number>; learned: number; updated_at: number }
type Change = { kind: string; before: string; after: string; edits?: { from: string; to: string }[] }
type State = { lessons: Lesson[]; learned_at: number | null; summary?: string; rounds: Round[]; learning: boolean; pending: number }

const TRIGGER: Record<string, string> = { 'before-ai': '发批注给 AI 前', publish: '推草稿箱时', idle: '停笔半小时后', manual: '手动学习时' }

/** What the workbench has learned from how the author edits AI drafts, and the edit history it learned from. */
export function Habits() {
  const toast = useToast()
  const [state, setState] = useState<State | null>(null)
  const [draft, setDraft] = useState<Lesson[] | null>(null)
  const [newRule, setNewRule] = useState('')
  const [open, setOpen] = useState<number | null>(null)
  const [changes, setChanges] = useState<Record<number, Change[]>>({})
  const load = () => api.get<State>('/api/learning').then(s => { setState(s); setDraft(d => d ?? s.lessons) }).catch(e => toast(e.message, 'error'))
  usePoll(load, 8000)

  const dirty = !!state && JSON.stringify(draft) !== JSON.stringify(state.lessons)
  async function save(next = draft) {
    try { const s = await api.put<State>('/api/learning/lessons', { lessons: next }); setState(st => st && { ...st, lessons: s.lessons }); setDraft(s.lessons); toast('已保存，下次起稿就按这些来') }
    catch (e) { toast((e as Error).message, 'error') }
  }
  async function learnNow() {
    try { await api.post('/api/learning/run'); toast('开始学了，一两分钟后刷新'); load() } catch (e) { toast((e as Error).message, 'error') }
  }
  async function toggle(rid: number) {
    if (open === rid) { setOpen(null); return }
    setOpen(rid)
    if (!changes[rid]) {
      const r = await api.get<{ changes: Change[] }>(`/api/learning/rounds/${rid}`)
      setChanges(c => ({ ...c, [rid]: r.changes }))
    }
  }
  const edit = (i: number, patch: Partial<Lesson>) => setDraft(d => (d || []).map((l, j) => (j === i ? { ...l, ...patch } : l)))

  if (!state || !draft) return <div className="page narrow"><div className="skeleton" style={{ height: 30, width: '40%' }} /></div>
  return (
    <div className="page narrow">
      <div className="page-head">
        <div>
          <h1>改稿习惯</h1>
          <p>从你在 AI 底稿上的真实改动和你写过的批注里总结出来的。之后起稿、按批注修改时，AI 都会照着这些来，少让你改同样的地方。</p>
        </div>
        <div className="actions">
          <button className="btn" disabled={state.learning} onClick={learnNow}>{state.learning ? <Ic.Loader size={14} /> : <Ic.Sparkle size={14} />} {state.learning ? '正在学' : '现在学一次'}</button>
        </div>
      </div>

      <div className="small muted" style={{ marginBottom: 12 }}>
        {state.pending ? `有 ${state.pending} 次改稿还没学，攒够 3 次会自动学。` : '改稿记录都学过了。'}
        {state.learned_at ? ` 上次学习 ${ago(state.learned_at)}${state.summary ? `：${state.summary}` : ''}` : ''}
      </div>

      <section className="settings-group">
        <h2>习惯（{draft.filter(l => l.enabled !== false).length} 条在用）</h2>
        <div className="card">
          {draft.length === 0 && <Empty icon={<Ic.Sparkle />} title="还没有学到习惯">你在 AI 写的稿子上改过几次之后，这里会出现总结。也可以先自己加几条。</Empty>}
          {draft.map((l, i) => (
            <div key={l.id || i} className={'setting habit' + (l.enabled === false ? ' off' : '')}>
              <input type="checkbox" checked={l.enabled !== false} aria-label="启用这条习惯" onChange={e => edit(i, { enabled: e.target.checked })} />
              <div className="body">
                <AutoTextarea className="habit-rule" value={l.rule} onChange={e => edit(i, { rule: e.target.value })} />
                <div className="d">{l.source === 'gary' ? '你加的' : `从 ${l.evidence || 1} 处改动里学到`}</div>
                {(l.examples || []).filter(e => e.before || e.after).map((e, k) => (
                  <div key={k} className="habit-ex"><del>{e.before}</del><span className="faint">→</span><ins>{e.after}</ins></div>
                ))}
              </div>
              <div className="side-actions">
                <button className="btn ghost sm danger" aria-label="删除这条习惯" onClick={() => setDraft(d => (d || []).filter((_, j) => j !== i))}><Ic.Trash size={13} /></button>
              </div>
            </div>
          ))}
          <div className="setting">
            <span className="ic"><Ic.Plus size={16} /></span>
            <div className="body"><div className="inline-form" style={{ marginTop: 0 }}>
              <input className="input" value={newRule} onChange={e => setNewRule(e.target.value)} placeholder="自己加一条，比如：结尾不要升华，聊完就收" />
              <button className="btn" disabled={!newRule.trim()} onClick={() => { setDraft(d => [...(d || []), { id: '', rule: newRule.trim(), enabled: true, source: 'gary' }]); setNewRule('') }}>加上</button>
            </div></div>
          </div>
        </div>
        {dirty && <div className="row" style={{ justifyContent: 'flex-end', marginTop: 10, gap: 8 }}>
          <button className="btn ghost" onClick={() => setDraft(state.lessons)}>不改了</button>
          <button className="btn primary" onClick={() => save()}>保存</button>
        </div>}
      </section>

      <section className="settings-group">
        <h2>改稿记录</h2>
        {state.rounds.length === 0 ? <div className="card"><Empty icon={<Ic.History />} title="还没有改稿记录">你在 AI 写的稿子上动过手，停笔半小时、发批注给 AI 前、或者推草稿箱时，会记一次。</Empty></div> : (
          <div className="card list-rows">
            {state.rounds.map(r => (
              <div key={r.id}>
                <button className="list-row" onClick={() => toggle(r.id)}>
                  <span className="src-icon"><Ic.Pen size={15} /></span>
                  <div className="grow">
                    <div className="t">{r.title || '（未命名稿件）'}</div>
                    <div className="sub">{TRIGGER[r.trigger] || r.trigger}{r.stats.title ? ' · 改了标题' : ''} · 改写 {r.stats.rewrite || 0} 段、删 {r.stats.delete || 0} 段、加 {r.stats.insert || 0} 段{r.learned ? ' · 已学' : ''}</div>
                  </div>
                  <span className="when">{ago(r.updated_at)}</span>
                </button>
                {open === r.id && (
                  <div className="round-detail">
                    <div className="row small"><a onClick={() => go(`drafts/${r.draft_id}`)}>打开这篇稿子</a></div>
                    {(changes[r.id] || []).map((c, i) => (
                      <div key={i} className="round-change">
                        {c.kind === 'title' && <div className="lbl">标题</div>}
                        {c.before && <div><span className="lbl">AI 写的</span><del>{c.before}</del></div>}
                        {c.after && <div><span className="lbl">你改成</span><ins>{c.after}</ins></div>}
                        {c.edits && c.edits.length > 0 && <div className="edits">{c.edits.map((e, k) => (
                          <span key={k} className="edit-chip">{e.from ? <del>{e.from}</del> : null}{e.from && e.to ? ' → ' : ''}{e.to ? <ins>{e.to}</ins> : null}</span>
                        ))}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
