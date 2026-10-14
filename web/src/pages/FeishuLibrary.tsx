import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { ago, Dialog, useToast } from '../ui'
import * as Ic from '../icons'

type Doc = {
  token: string; title: string; type: string; url: string; created: number; updated: number
  category: 'own' | 'generated' | 'meeting' | 'admin' | 'others' | 'untitled' | 'unsure'
  published: boolean; imported: boolean; queued: boolean; failed?: string; suggested: boolean
}
type Library = { items: Doc[]; updated_at: number | null; refreshing: boolean }

const CAT_LABEL: Record<string, string> = {
  own: '你写的', generated: 'AI 生成', meeting: '会议记录', admin: '行政表单', others: '别人的分享', untitled: '无标题', unsure: '未分类',
}
const FILTERS = [['suggested', '建议导入'], ['own', '你写的'], ['published', '已发公众号'], ['rest', '其他']] as const
type Filter = typeof FILTERS[number][0]

const importable = (d: Doc) => ['docx', 'doc', 'wiki', 'sheet', 'bitable'].includes(d.type) && !d.imported && !d.queued

export function FeishuLibrary({ open, onClose, onImported }: { open: boolean; onClose: () => void; onImported: () => void }) {
  const toast = useToast()
  const [lib, setLib] = useState<Library | null>(null)
  const [filter, setFilter] = useState<Filter>('suggested')
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    let alive = true
    const load = () => api.get<Library>('/api/feishu/library').then(l => { if (alive) setLib(l) }).catch(e => toast(e.message, 'error'))
    load()
    const t = window.setInterval(load, 5000)
    return () => { alive = false; window.clearInterval(t) }
  }, [open, toast])

  const shown = useMemo(() => {
    const items = lib?.items || []
    const byFilter = items.filter(d => {
      if (filter === 'suggested') return d.suggested
      if (filter === 'own') return d.category === 'own'
      if (filter === 'published') return d.published
      return d.category !== 'own' && !d.published
    })
    const needle = q.trim().toLowerCase()
    return needle ? byFilter.filter(d => d.title.toLowerCase().includes(needle)) : byFilter
  }, [lib, filter, q])

  const counts = useMemo(() => {
    const items = lib?.items || []
    return {
      suggested: items.filter(d => d.suggested && importable(d)).length,
      own: items.filter(d => d.category === 'own').length,
      published: items.filter(d => d.published).length,
      rest: items.filter(d => d.category !== 'own' && !d.published).length,
    }
  }, [lib])

  function toggle(token: string) {
    setPicked(p => { const n = new Set(p); if (n.has(token)) n.delete(token); else n.add(token); return n })
  }
  function pickAllShown() {
    const can = shown.filter(importable).map(d => d.token)
    setPicked(p => can.every(t => p.has(t)) ? new Set([...p].filter(t => !can.includes(t))) : new Set([...p, ...can]))
  }
  async function doImport() {
    setBusy(true)
    try {
      const r = await api.post<{ queued: number }>('/api/feishu/import', { tokens: [...picked] })
      toast(`已加入队列 ${r.queued} 篇，导入后会自动提炼选题`)
      setPicked(new Set()); onImported()
      window.dispatchEvent(new Event('cw-refresh-status'))
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  async function refresh() {
    await api.post('/api/feishu/library/refresh')
    setLib(l => l && { ...l, refreshing: true })
  }

  const allShownPicked = shown.filter(importable).length > 0 && shown.filter(importable).every(d => picked.has(d.token))

  return (
    <Dialog open={open} onClose={onClose} title="从飞书导入" wide
      footer={<>
        <span className="small muted grow" style={{ alignSelf: 'center' }}>
          {lib?.refreshing ? '正在更新文档列表…' : lib?.updated_at ? `文档列表更新于 ${ago(lib.updated_at)}` : ''}
          {!lib?.refreshing && <button className="disclosure" style={{ marginLeft: 8 }} onClick={refresh}>重新扫描</button>}
        </span>
        <button className="btn" onClick={onClose}>关闭</button>
        <button className="btn primary" disabled={!picked.size || busy} onClick={doImport}>
          {busy ? <Ic.Loader size={15} /> : <Ic.Sparkle size={15} />} 导入并提炼{picked.size ? `（${picked.size}）` : ''}
        </button>
      </>}>
      <div className="small muted" style={{ marginBottom: 12 }}>
        你在飞书里创建的文档都在这里。“建议导入”是你自己写、还没发过公众号的；AI 生成的报告、会议记录和行政表单默认不导入，选题提炼也只摘你自己的话。
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="segmented" role="group" aria-label="筛选">
          {FILTERS.map(([k, l]) => (
            <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}<span className="n">{counts[k]}</span></button>
          ))}
        </div>
        <input className="input grow" style={{ minHeight: 34, height: 34 }} placeholder="搜标题" value={q} onChange={e => setQ(e.target.value)} />
      </div>

      {!lib || (lib.refreshing && !lib.items.length) ? (
        <div className="stack" style={{ paddingBottom: 12 }}>{[0, 1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 44 }} />)}</div>
      ) : shown.length === 0 ? (
        <div className="empty"><b>没有符合条件的文档</b></div>
      ) : (
        <div className="card list-rows" style={{ marginBottom: 6, maxHeight: '52vh', overflow: 'auto' }}>
          <label className="list-row" style={{ cursor: 'pointer', background: 'var(--surface-2)' }}>
            <input type="checkbox" checked={allShownPicked} onChange={pickAllShown} aria-label="全选当前列表" />
            <span className="small muted grow">全选这一页能导入的（{shown.filter(importable).length} 篇）</span>
          </label>
          {shown.map(d => {
            const can = importable(d)
            return (
              <label key={d.token} className="list-row" style={{ cursor: can ? 'pointer' : 'default', opacity: can || d.imported || d.queued ? 1 : .65 }}>
                <input type="checkbox" disabled={!can} checked={picked.has(d.token)} onChange={() => toggle(d.token)} aria-label={d.title} />
                <span className="src-icon" style={{ width: 28, height: 28 }}>{d.type === 'sheet' || d.type === 'bitable' ? <Ic.Inbox size={14} /> : <Ic.Doc size={14} />}</span>
                <div className="grow">
                  <div className="t">{d.title}</div>
                  <div className="sub">
                    {new Date(d.created * 1000).toLocaleDateString('zh-CN')}
                    {d.category !== 'own' && ` · ${CAT_LABEL[d.category]}`}
                    {(d.type === 'sheet' || d.type === 'bitable') && ' · 表格'}
                    {d.failed && ` · 上次导入失败：${d.failed}`}
                  </div>
                </div>
                {d.published && <span className="pill">已发公众号</span>}
                {d.imported && <span className="pill ok"><Ic.Check size={12} /> 已导入</span>}
                {d.queued && <span className="pill accent"><Ic.Loader size={12} /> 排队中</span>}
                <a className="btn ghost icon sm" href={d.url} target="_blank" rel="noreferrer" aria-label="在飞书打开" onClick={e => e.stopPropagation()}><Ic.External size={13} /></a>
              </label>
            )
          })}
        </div>
      )}
    </Dialog>
  )
}
