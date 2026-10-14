import { useEffect, useRef, useState } from 'react'
import { api, type Draft, type XhsCard, type XhsState } from '../api'
import { AutoTextarea, useToast } from '../ui'
import { imgUrl } from './Longform'
import * as Ic from '../icons'

const TITLE_MAX = 20, BODY_MAX = 1000

function caption(s: XhsState) {
  return `${s.title || ''}\n\n${s.body || ''}\n\n${(s.tags || []).map(t => '#' + t).join(' ')}`.trim()
}

/** 小红书 version of a long-form draft: AI rewrite + drawn cards; the author sends it from his phone. */
export function XhsPanel({ draft, onDraft }: { draft: Draft; onDraft: (d: Draft) => void }) {
  const toast = useToast()
  const [s, setS] = useState<XhsState>(draft.xhs || {})
  const [busy, setBusy] = useState('')
  const [cardsOpen, setCardsOpen] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => { setS(draft.xhs || {}) }, [draft.id, draft.xhs?.generated_at, draft.xhs?.generating, draft.xhs?.card_images?.join(',')])

  // poll while the AI is writing it
  useEffect(() => {
    if (!s.generating) return
    const t = window.setInterval(async () => {
      const d = await api.get<Draft>(`/api/drafts/${draft.id}`)
      if (!d.xhs?.generating) { onDraft(d); setS(d.xhs || {}); if (d.xhs?.error) toast(d.xhs.error, 'error') }
    }, 4000)
    return () => window.clearInterval(t)
  }, [s.generating, draft.id, onDraft, toast])

  function patch(next: Partial<XhsState>, now = false) {
    const merged = { ...s, ...next }
    setS(merged)
    window.clearTimeout(timer.current)
    const send = () => api.put<XhsState>(`/api/drafts/${draft.id}/xhs`, next).catch(e => toast((e as Error).message, 'error'))
    if (now) send(); else timer.current = window.setTimeout(send, 700)
  }
  async function generate() {
    if (s.title && !window.confirm('按现在的长文重新生成小红书版？你改过的文案和图卡文字会被覆盖。')) return
    try { setS(await api.post<XhsState>(`/api/drafts/${draft.id}/xhs/generate`)) } catch (e) { toast((e as Error).message, 'error') }
  }
  async function rerender() {
    setBusy('render')
    try {
      await api.put(`/api/drafts/${draft.id}/xhs`, { cover: s.cover, cards: s.cards })
      const next = await api.post<XhsState>(`/api/drafts/${draft.id}/xhs/render`)
      setS(next); onDraft(await api.get<Draft>(`/api/drafts/${draft.id}`)); toast('图卡重新画好了')
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(caption(s)); toast('文案已复制，标题、正文和话题都在里面') } catch { toast('复制失败，手动选中复制吧', 'error') }
  }
  const order = [...(s.card_images || []), ...(s.extra_images || [])]
  async function share() {
    setBusy('share')
    try {
      await navigator.clipboard.writeText(caption(s)).catch(() => {})
      const files = await Promise.all(order.map(async (id, n) => {
        const blob = await (await fetch(imgUrl(id))).blob()
        return new File([blob], `${String(n + 1).padStart(2, '0')}.${blob.type.includes('png') ? 'png' : 'jpg'}`, { type: blob.type })
      }))
      if (!navigator.canShare || !navigator.canShare({ files })) {
        toast('这台设备不支持直接分享图片，用“下载图片”再到小红书里选', 'error'); return
      }
      await navigator.share({ files })
      toast('文案已经复制好，在小红书里粘贴进正文，存草稿或发布')
    } catch (e) {
      if ((e as Error).name !== 'AbortError') toast((e as Error).message, 'error')
    } finally { setBusy('') }
  }
  function move(list: 'card_images' | 'extra_images', i: number, d: -1 | 1) {
    const arr = [...(s[list] || [])]
    const j = i + d
    if (j < 0 || j >= arr.length) return
    ;[arr[i], arr[j]] = [arr[j], arr[i]]
    patch({ [list]: arr }, true)
  }
  const candidates = (draft.images || []).filter(im => !order.includes(im.id) && !im.name.startsWith('小红书-'))
  const setCard = (i: number, c: XhsCard) => setS(st => ({ ...st, cards: (st.cards || []).map((x, j) => (j === i ? c : x)) }))

  if (s.generating) return <div className="stack"><div className="row muted small"><Ic.Loader size={14} /> 正在把长文改成小红书版、画图卡，一两分钟。</div><div className="skeleton" style={{ height: 160 }} /></div>
  if (!s.title && !s.body) return (
    <div className="stack">
      <div className="small muted">按这篇长文生成一篇小红书图文：20 字以内的标题、1000 字以内的正文、话题标签，再画一张封面卡和几张要点卡。小红书没有开放发布接口，最后在手机上点“分享到小红书”，在小红书里存草稿或发布。</div>
      {s.error && <div className="err small">{s.error}</div>}
      <button className="btn primary" onClick={generate}><Ic.Sparkle size={14} /> 生成小红书版</button>
    </div>
  )

  return (
    <div className="stack xhs">
      <div className="xhs-actions">
        <button className="btn primary sm mobile-share" disabled={!!busy || !order.length} onClick={share}>{busy === 'share' ? <Ic.Loader size={14} /> : <Ic.Send size={14} />} 分享到小红书</button>
        <button className="btn sm" onClick={copy}>复制文案</button>
        <a className="btn sm" href={`/api/drafts/${draft.id}/xhs.zip`}><Ic.Upload size={14} /> 下载图片</a>
        <button className="btn ghost sm" onClick={generate}>重新生成</button>
      </div>
      <div className="small faint">手机上点“分享到小红书”：图片按顺序带进小红书，文案已经复制好，粘贴进正文就行。电脑上用“下载图片”和“复制文案”。</div>

      <section>
        <div className="mp-head">标题 <span className={'faint small' + ((s.title || '').length > TITLE_MAX ? ' over' : '')}>{(s.title || '').length}/{TITLE_MAX}</span></div>
        <input className="input" value={s.title || ''} maxLength={TITLE_MAX} onChange={e => patch({ title: e.target.value })} />
      </section>
      <section>
        <div className="mp-head">正文 <span className="faint small">{(s.body || '').length}/{BODY_MAX}</span></div>
        <AutoTextarea className="textarea xhs-body" maxLength={BODY_MAX} value={s.body || ''} onChange={e => patch({ body: e.target.value })} />
      </section>
      <section>
        <div className="mp-head">话题 <span className="faint small">空格隔开</span></div>
        <input className="input" value={(s.tags || []).join(' ')} onChange={e => patch({ tags: e.target.value.split(/[\s,，#]+/).filter(Boolean) })} />
      </section>

      <section>
        <div className="mp-head">图片 <span className="faint small">{order.length} 张，按这个顺序发</span></div>
        <div className="xhs-grid">
          {(s.card_images || []).map((id, i) => (
            <div key={id} className="xhs-tile"><img src={imgUrl(id)} alt="" />
              <div className="tile-acts"><button onClick={() => move('card_images', i, -1)} aria-label="往前">‹</button><span>{i === 0 ? '封面' : `卡 ${i}`}</span><button onClick={() => move('card_images', i, 1)} aria-label="往后">›</button></div></div>
          ))}
          {(s.extra_images || []).map((id, i) => (
            <div key={id} className="xhs-tile"><img src={imgUrl(id)} alt="" />
              <div className="tile-acts"><button onClick={() => move('extra_images', i, -1)} aria-label="往前">‹</button>
                <button onClick={() => patch({ extra_images: (s.extra_images || []).filter(x => x !== id) }, true)} aria-label="不要这张">×</button>
                <button onClick={() => move('extra_images', i, 1)} aria-label="往后">›</button></div></div>
          ))}
        </div>
        {candidates.length > 0 && (
          <details className="adv"><summary><Ic.Plus size={12} /> 从长文的图片里加</summary>
            <div className="xhs-grid" style={{ marginTop: 8 }}>
              {candidates.map(im => <button key={im.id} className="xhs-tile add" onClick={() => patch({ extra_images: [...(s.extra_images || []), im.id] }, true)}><img src={imgUrl(im.id)} alt="" /></button>)}
            </div>
          </details>
        )}
      </section>

      <section>
        <button className="btn ghost sm" onClick={() => setCardsOpen(v => !v)}><Ic.Note size={13} /> {cardsOpen ? '收起图卡文字' : '改图卡上的字'}</button>
        {cardsOpen && (
          <div className="stack" style={{ marginTop: 8 }}>
            <div className="small muted">封面</div>
            <input className="input" placeholder="顶上一句引子" value={s.cover?.kicker || ''} onChange={e => setS(st => ({ ...st, cover: { ...st.cover, kicker: e.target.value } }))} />
            <AutoTextarea className="textarea" placeholder="大字，每行一句" value={(s.cover?.lines || []).join('\n')} onChange={e => setS(st => ({ ...st, cover: { ...st.cover, lines: e.target.value.split('\n') } }))} />
            <input className="input" placeholder="大字里要标亮的词" value={s.cover?.highlight || ''} onChange={e => setS(st => ({ ...st, cover: { ...st.cover, highlight: e.target.value } }))} />
            <input className="input" placeholder="副标题" value={s.cover?.subtitle || ''} onChange={e => setS(st => ({ ...st, cover: { ...st.cover, subtitle: e.target.value } }))} />
            {(s.cards || []).map((c, i) => (
              <div key={i} className="stack" style={{ gap: 6 }}>
                <div className="small muted">要点卡 {i + 1}</div>
                <input className="input" value={c.title || ''} onChange={e => setCard(i, { ...c, title: e.target.value })} />
                <AutoTextarea className="textarea" value={(c.points || []).join('\n')} onChange={e => setCard(i, { ...c, points: e.target.value.split('\n') })} />
              </div>
            ))}
            <button className="btn sm" disabled={busy === 'render'} onClick={rerender}>{busy === 'render' ? <Ic.Loader size={14} /> : <Ic.Image size={14} />} 按这些字重新出图</button>
          </div>
        )}
      </section>
    </div>
  )
}
