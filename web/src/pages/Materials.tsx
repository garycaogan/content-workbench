import { useState } from 'react'
import { api, SOURCE_LABEL, type Idea, type Material } from '../api'
import { go, startCapture } from '../App'
import { ago, Empty, SourceIcon, usePoll, useToast } from '../ui'
import { IdeaCard } from './Ideas'
import * as Ic from '../icons'
import { FeishuLibrary } from './FeishuLibrary'

export function Materials({ openId }: { openId?: number }) {
  const toast = useToast()
  const [items, setItems] = useState<Material[] | null>(null)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const load = () => api.get<Material[]>('/api/materials').then(setItems).catch(e => toast(e.message, 'error'))
  usePoll(load, 7000)

  const selected = openId ?? undefined

  return (
    <div className={'split' + (selected ? ' has-detail' : '')}>
      <div className="split-list">
        <div className="head">
          <div className="row">
            <h1 className="grow">素材</h1>
            <button className="btn sm" onClick={() => setLibraryOpen(true)}><Ic.Feishu size={14} /> 从飞书导入</button>
            <button className="btn sm" onClick={startCapture}><Ic.Plus size={14} /> 记一笔</button>
          </div>
          <div className="small muted">录音、随手记、飞书文档和妙记，按时间排</div>
        </div>
        {items === null ? (
          <div className="stack" style={{ padding: 16 }}>{[0, 1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 44 }} />)}</div>
        ) : items.length === 0 ? (
          <Empty icon={<Ic.Inbox />} title="还没有素材">点“记一笔”，写点东西或者录一段。</Empty>
        ) : (
          <div className="list-rows">
            {items.map(m => (
              <button key={m.id} className="list-row" aria-current={selected === m.id ? 'true' : undefined} onClick={() => go(`materials/${m.id}`)}>
                <SourceIcon source={m.source} />
                <div className="grow">
                  <div className="t">{m.title}</div>
                  <div className="sub">
                    {m.status === 'transcribing' ? '转写中…' : m.status === 'failed' ? '处理失败' :
                      `${SOURCE_LABEL[m.source] || m.source}${m.idea_count ? ` · ${m.idea_count} 个选题` : ''}${m.length ? ` · ${m.length} 字` : ''}`}
                  </div>
                </div>
                <span className="when">{ago(m.created_at)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <FeishuLibrary open={libraryOpen} onClose={() => setLibraryOpen(false)} onImported={load} />
      <div className="split-detail">
        {selected ? <MaterialDetail key={selected} id={selected} onChanged={load} /> : (
          <Empty icon={<Ic.Inbox />} title="选一条素材">左边点一条，看全文和从它提炼出的选题。</Empty>
        )}
      </div>
    </div>
  )
}

function MaterialDetail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const toast = useToast()
  const [m, setM] = useState<Material | null>(null)
  const [ideas, setIdeas] = useState<Idea[]>([])
  const [full, setFull] = useState(false)

  const load = () => {
    api.get<Material>(`/api/materials/${id}`).then(setM).catch(e => toast(e.message, 'error'))
    api.get<Idea[]>('/api/ideas?status=all').then(all => setIdeas(all.filter(i => i.material_id === id))).catch(() => {})
  }
  usePoll(load, 6000, [id])

  async function extract() {
    try { await api.post(`/api/materials/${id}/extract`); toast('已重新排队提炼，好了会出现在下面') } catch (e) { toast((e as Error).message, 'error') }
  }
  async function remove() {
    if (!window.confirm('删除这条素材？已经提炼出的选题会保留。')) return
    try { await api.del(`/api/materials/${id}`); onChanged(); go('materials') } catch (e) { toast((e as Error).message, 'error') }
  }

  if (!m) return <div className="doc"><div className="skeleton" style={{ height: 28, width: '60%' }} /></div>
  const body = m.body || ''
  const long = body.length > 2400

  return (
    <div className="doc">
      <button className="btn ghost sm mobile-only" style={{ marginBottom: 12 }} onClick={() => go('materials')}><Ic.Back size={14} /> 素材</button>
      <h1>{m.title}</h1>
      <div className="doc-meta">
        <span className="pill">{SOURCE_LABEL[m.source] || m.source}</span>
        <span>{ago(m.created_at)}</span>
        {m.length ? <span>{m.length} 字</span> : null}
        <span className="grow" />
        <button className="btn sm" onClick={extract} disabled={m.status !== 'ready'}><Ic.Sparkle size={14} /> 重新提炼</button>
        <button className="btn ghost sm danger" onClick={remove} aria-label="删除素材"><Ic.Trash size={14} /></button>
      </div>
      {m.error && <div className="callout" style={{ marginBottom: 16 }}><Ic.Alert size={15} /><span>{m.error}</span></div>}

      <h2 className="section-title"><Ic.Bulb size={15} /> 提炼出的选题 {ideas.length > 0 && <span className="faint">{ideas.length}</span>}</h2>
      {ideas.length === 0 ? (
        <div className="card card-pad muted small" style={{ marginBottom: 24 }}>
          {m.status === 'transcribing' ? '转写完会自动提炼。' : '还没有。提炼在后台进行，一般一两分钟；如果一直没有，说明这段里没找到值得写的。'}
        </div>
      ) : (
        <div className="card" style={{ marginBottom: 28 }}>{ideas.map(i => <IdeaCard key={i.id} idea={i} onChange={load} hideSource />)}</div>
      )}

      <h2 className="section-title"><Ic.Doc size={15} /> 全文</h2>
      {m.status === 'transcribing' ? (
        <div className="stack">{[90, 96, 70].map((w, i) => <div key={i} className="skeleton" style={{ height: 14, width: w + '%' }} />)}</div>
      ) : (
        <>
          <div className="transcript">{long && !full ? body.slice(0, 2400) + '…' : body}</div>
          {long && <button className="btn sm" style={{ marginTop: 12 }} onClick={() => setFull(v => !v)}>{full ? '收起' : `展开全文（${body.length} 字）`}</button>}
        </>
      )}
    </div>
  )
}
