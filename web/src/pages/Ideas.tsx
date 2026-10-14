import { useMemo, useState } from 'react'
import { api, type Idea } from '../api'
import { go } from '../App'
import { ago, AutoTextarea, Dialog, Empty, usePoll, useToast } from '../ui'
import * as Ic from '../icons'

const FILTERS: [string, string][] = [['new', '待挑'], ['drafted', '已起草'], ['dropped', '不要了'], ['all', '全部']]

export function Ideas() {
  const toast = useToast()
  const [filter, setFilter] = useState('new')
  const [ideas, setIdeas] = useState<Idea[] | null>(null)
  const [adding, setAdding] = useState(false)
  const [q, setQ] = useState('')
  const [platform, setPlatform] = useState<'all' | '公众号' | 'X'>('all')
  const [limit, setLimit] = useState(30)

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (ideas || []).filter(i =>
      (platform === 'all' || i.platforms.includes(platform)) &&
      (!needle || `${i.title} ${i.angle} ${i.material_title || ''}`.toLowerCase().includes(needle)))
  }, [ideas, q, platform])

  const load = () => api.get<Idea[]>(`/api/ideas?status=${filter}`).then(setIdeas).catch(e => toast(e.message, 'error'))
  usePoll(load, 8000, [filter])

  return (
    <div className="page narrow">
      <div className="page-head">
        <div>
          <h1>选题</h1>
          <p>从你的录音和笔记里提炼出来的，只摘你自己说过的话。挑一个，点写成公众号或 X。</p>
        </div>
        <div className="actions"><button className="btn" onClick={() => setAdding(true)}><Ic.Plus size={15} /> 自己加一个</button></div>
      </div>

      <div className="row" style={{ marginBottom: 14 }}>
        <div className="segmented" role="group" aria-label="状态">
          {FILTERS.map(([k, l]) => <button key={k} aria-pressed={filter === k} onClick={() => { setIdeas(null); setFilter(k); setLimit(30) }}>{l}</button>)}
        </div>
        <div className="segmented" role="group" aria-label="平台">
          {(['all', '公众号', 'X'] as const).map(k => <button key={k} aria-pressed={platform === k} onClick={() => { setPlatform(k); setLimit(30) }}>{k === 'all' ? '全部平台' : k}</button>)}
        </div>
        <input className="input grow" style={{ minHeight: 34, height: 34, minWidth: 160 }} placeholder="搜选题、角度、来源" value={q} onChange={e => { setQ(e.target.value); setLimit(30) }} />
      </div>
      {ideas && ideas.length > 0 && <div className="small muted" style={{ margin: '-4px 0 10px 2px' }}>{shown.length} 个选题{q || platform !== 'all' ? '符合条件' : ''}</div>}

      {ideas === null ? <SkeletonCards /> : ideas.length === 0 ? (
        <div className="card"><Empty icon={<Ic.Bulb />} title={filter === 'new' ? '没有待挑的选题' : '这里是空的'}>
          记一笔或者导入一段录音，几分钟后这里就会出现新选题。
        </Empty></div>
      ) : (
        <>
          <div className="card">{shown.slice(0, limit).map(i => <IdeaCard key={i.id} idea={i} onChange={load} />)}</div>
          {shown.length > limit && (
            <div className="row" style={{ justifyContent: 'center', marginTop: 14 }}>
              <button className="btn" onClick={() => setLimit(l => l + 30)}>再看 30 个（还有 {shown.length - limit} 个）</button>
            </div>
          )}
        </>
      )}

      <AddIdea open={adding} onClose={() => setAdding(false)} onAdded={load} />
    </div>
  )
}

export function IdeaCard({ idea, onChange, compact, hideSource }: { idea: Idea; onChange: () => void; compact?: boolean; hideSource?: boolean }) {
  const toast = useToast()
  const [showQuotes, setShowQuotes] = useState(false)
  const [busy, setBusy] = useState('')

  async function patch(body: object, msg?: string) {
    try { await api.patch(`/api/ideas/${idea.id}`, body); if (msg) toast(msg); onChange(); window.dispatchEvent(new Event('cw-refresh-status')) }
    catch (e) { toast((e as Error).message, 'error') }
  }
  async function draft(platform: 'wechat' | 'x') {
    setBusy(platform)
    try {
      const r = await api.post<{ id: number }>(`/api/ideas/${idea.id}/draft`, { platform })
      go(`drafts/${r.id}`)
    } catch (e) { toast((e as Error).message, 'error'); setBusy('') }
  }

  const writeButtons = (
    <>
      <button className="btn sm" disabled={!!busy} onClick={() => draft('wechat')}>
        {busy === 'wechat' ? <Ic.Loader size={14} /> : <Ic.WechatLogo size={14} />} 写成公众号
      </button>
      <button className="btn sm" disabled={!!busy} onClick={() => draft('x')}>
        {busy === 'x' ? <Ic.Loader size={14} /> : <Ic.XLogo size={12} />} 写成 X
      </button>
    </>
  )

  if (compact) {
    return (
      <div className="idea-compact">
        <div className="t">{idea.starred ? <Ic.Star size={13} filled className="star on" /> : null} {idea.title}</div>
        {idea.angle && <div className="a">{idea.angle}</div>}
        <div className="row" style={{ marginTop: 4 }}>{writeButtons}</div>
      </div>
    )
  }

  return (
    <article className="idea">
      <div className="idea-head">
        <h3 className="grow">{idea.title}</h3>
        <button className={'btn ghost icon sm'} aria-label={idea.starred ? '取消星标' : '星标'} aria-pressed={!!idea.starred}
          onClick={() => patch({ starred: !idea.starred })}>
          <Ic.Star size={16} filled={!!idea.starred} className={'star' + (idea.starred ? ' on' : '')} />
        </button>
      </div>
      {idea.angle && <p className="angle">{idea.angle}</p>}

      {idea.quotes.length > 0 && (
        <>
          <button className="disclosure" style={{ marginTop: 10 }} aria-expanded={showQuotes} onClick={() => setShowQuotes(v => !v)}>
            {showQuotes ? <Ic.Down size={14} /> : <Ic.Right size={14} />} 你的原话 {idea.quotes.length} 句
          </button>
          {showQuotes && <ul className="quotes">{idea.quotes.map((q, n) => <li key={n}>{q}</li>)}</ul>}
        </>
      )}

      {idea.notes && (
        <div className="callout" style={{ marginTop: 12 }}><Ic.Alert size={15} /><span>{idea.notes}</span></div>
      )}

      <div className="actions">
        {idea.status === 'dropped' ? (
          <button className="btn sm" onClick={() => patch({ status: 'new' }, '已放回待挑')}><Ic.Undo size={14} /> 放回待挑</button>
        ) : writeButtons}
        <span className="spacer" />
        <div className="meta" style={{ marginTop: 0 }}>
          {idea.material_title && !hideSource && (
            <a className="src" href={idea.material_id ? `#/materials/${idea.material_id}` : undefined} title={idea.material_title}>
              <Ic.Inbox size={13} /> {idea.material_title}
            </a>
          )}
          <span>{ago(idea.created_at)}</span>
          {idea.status === 'drafted' && <span className="pill accent">已起草</span>}
        </div>
        {idea.status !== 'dropped' && (
          <button className="btn ghost sm" onClick={() => patch({ status: 'dropped' }, '已移到“不要了”')}>不要了</button>
        )}
      </div>
    </article>
  )
}

function AddIdea({ open, onClose, onAdded }: { open: boolean; onClose: () => void; onAdded: () => void }) {
  const toast = useToast()
  const [title, setTitle] = useState('')
  const [angle, setAngle] = useState('')
  async function add() {
    try {
      await api.post('/api/ideas', { title, angle })
      setTitle(''); setAngle(''); onClose(); onAdded(); toast('选题已加入')
    } catch (e) { toast((e as Error).message, 'error') }
  }
  return (
    <Dialog open={open} onClose={onClose} title="自己加一个选题"
      footer={<><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={!title.trim()} onClick={add}>加入</button></>}>
      <div className="stack">
        <label className="field"><span>选题</span><input className="input" autoFocus value={title} onChange={e => setTitle(e.target.value)} placeholder="一句话说清楚要写什么" /></label>
        <label className="field"><span>想怎么讲（可选）</span><AutoTextarea className="textarea" value={angle} onChange={e => setAngle(e.target.value)} style={{ minHeight: 90 }} placeholder="切入角度、想举的例子" /></label>
      </div>
    </Dialog>
  )
}

export function SkeletonCards({ n = 3 }: { n?: number }) {
  return (
    <div className="card">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="idea">
          <div className="skeleton" style={{ height: 18, width: '62%' }} />
          <div className="skeleton" style={{ height: 13, width: '92%', marginTop: 12 }} />
          <div className="skeleton" style={{ height: 13, width: '78%', marginTop: 8 }} />
        </div>
      ))}
    </div>
  )
}
