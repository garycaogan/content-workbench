import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Draft, type DraftImage, type Publication, type RevisionInfo, type Status, type Thread } from '../api'
import { go } from '../App'
import { ago, AutoTextarea, Dialog, PlatformPill, useToast } from '../ui'
import * as Ic from '../icons'
import { CasesPanel, FeedCard, FeishuLine, MediaPanel, PublishLongform, uploadImages } from './Longform'
import { RichEditor, type Selection } from '../RichEditor'
import { CommentsPanel, DiffDialog, waiting } from './Comments'
import { XhsPanel } from './Xhs'
import { VideoCard } from './Video'
import { locate, type Anchor } from '../commentAnchors'
import type { Editor as TiptapEditor } from '@tiptap/react'

type Preview = { flags: string[]; thread?: { index: number; text: string; weighted: number; over: boolean }[]; html?: string }
type Version = { id: number; title: string; body: string; created_at: number; origin: string }
type SideTab = 'preview' | 'comments' | 'media' | 'xhs' | 'video' | 'cases' | 'notes' | 'voice' | 'log'

const SEP = '\n---\n'
const SPLIT = /\n[ \t]*-{3,}[ \t]*\n/
const X_LIMIT = 280

/** twitter-text v3 weighting: most CJK and emoji count 2, URLs count 23. */
export function xLength(text: string): number {
  let n = 0
  const urls = text.match(/https?:\/\/\S+/g) || []
  let rest = text
  for (const u of urls) { rest = rest.replace(u, ''); n += 23 }
  for (const ch of rest) {
    const c = ch.codePointAt(0) || 0
    const light = c <= 4351 || (c >= 8192 && c <= 8205) || (c >= 8208 && c <= 8223) || (c >= 8242 && c <= 8247)
    n += light ? 1 : 2
  }
  return n
}

function normalizeThread(body: string) {
  return body.split(SPLIT).map(s => s.trim()).join(SEP)
}

const SIDE_KEY = 'cw-editor-side'

export function Editor({ id, status }: { id: number; status: Status | null }) {
  const toast = useToast()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [notes, setNotes] = useState('')
  const [summary, setSummary] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [saved, setSaved] = useState<'saved' | 'saving' | 'dirty'>('saved')
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [tab, setTab] = useState<SideTab>('preview')
  const [sideOpen, setSideOpen] = useState(() => { try { return localStorage.getItem(SIDE_KEY) !== 'closed' } catch { return true } })
  const [confirming, setConfirming] = useState(false)
  const [versions, setVersions] = useState<Version[] | null>(null)
  const [author, setAuthor] = useState('')
  const [uploading, setUploading] = useState(false)
  const [threads, setThreads] = useState<Thread[]>([])
  const [activeThread, setActiveThread] = useState<number | null>(null)
  const [pendingSel, setPendingSel] = useState<Selection | null>(null)
  const [order, setOrder] = useState<Map<number, number | null>>(new Map())
  const [poll, setPoll] = useState(0)
  const [diffFor, setDiffFor] = useState<number | null>(null)
  const [hiddenRevision, setHiddenRevision] = useState<number | null>(null)
  const loaded = useRef(false)
  const lastStatus = useRef<string | null>(null)
  const richRef = useRef<TiptapEditor | null>(null)
  const latest = useRef({ title, body, notes, summary, sourceUrl })
  latest.current = { title, body, notes, summary, sourceUrl }

  const loadThreads = useCallback(() => api.get<Thread[]>(`/api/drafts/${id}/comments`).then(setThreads).catch(() => {}), [id])

  // load; keep polling while AI is drafting or revising, then take its text
  useEffect(() => {
    if (poll === 0) loaded.current = false
    let t: number | undefined
    const load = () => api.get<Draft>(`/api/drafts/${id}`).then(d => {
      setDraft(d)
      const finishedRevision = lastStatus.current === 'revising' && d.status !== 'revising'
      if (!loaded.current || d.status === 'generating' || finishedRevision) {
        setTitle(d.title); setBody(d.platform === 'x' ? normalizeThread(d.body) : d.body); setNotes(d.notes)
        setSummary(d.summary || ''); setSourceUrl(d.source_url || '')
        if (!loaded.current) setTab(d.platform === 'x' ? 'notes' : 'preview')
      }
      if (finishedRevision) {
        loadThreads()
        if (d.error) toast(d.error, 'error')
        else toast('AI 按批注改好了，右边能看每条的回复')
      }
      lastStatus.current = d.status
      if (d.status !== 'generating' && d.status !== 'revising') { loaded.current = true; window.clearInterval(t) }
    }).catch(e => toast(e.message, 'error'))
    load()
    loadThreads().then(() => undefined)
    t = window.setInterval(load, 4000)
    return () => window.clearInterval(t)
  }, [id, toast, poll, loadThreads])

  // open threads become highlights in the article
  const anchors: Anchor[] = threads.filter(t => t.status === 'open' && t.quote)
    .map(t => ({ id: t.id, quote: t.quote!, prefix: t.prefix, suffix: t.suffix }))
  const anchorKey = anchors.map(a => a.id).join(',')
  useEffect(() => {
    const ed = richRef.current
    const found = ed ? locate(ed.state.doc, anchors) : new Map()
    const next = new Map<number, number | null>()
    for (const t of threads) next.set(t.id, t.quote ? (found.get(t.id)?.[0] ?? null) : -1)
    setOrder(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threads, body, anchorKey])

  // opening a draft that has open comments shows them first
  const autoTab = useRef<number | null>(null)
  useEffect(() => {
    if (autoTab.current === id || !threads.length) return
    autoTab.current = id
    if (threads.some(t => t.status === 'open')) setTab('comments')
  }, [threads, id])

  function focusThread(tid: number | null) {
    setActiveThread(tid)
    if (tid == null) return
    setSideOpen(true); setTab('comments')
    window.setTimeout(() => document.querySelector(`.cm-anchor[data-cid="${tid}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 50)
  }

  async function revise() {
    if (saved !== 'saved') await save()
    try {
      await api.post(`/api/drafts/${id}/revise`)
      lastStatus.current = 'revising'
      setDraft(d => d && { ...d, status: 'revising' })
      setPoll(n => n + 1)
    } catch (e) { toast((e as Error).message, 'error') }
  }

  async function undoRevision() {
    if (!window.confirm('撤回 AI 这一轮的修改，正文回到你发批注之前的样子？批注和回复会保留。')) return
    try {
      await api.post(`/api/drafts/${id}/undo-revision`)
      const d = await api.get<Draft>(`/api/drafts/${id}`)
      setDraft(d); setTitle(d.title); setBody(d.body); toast('已撤回这一轮')
    } catch (e) { toast((e as Error).message, 'error') }
  }

  useEffect(() => { api.get<{ author: string }>('/api/settings').then(s => setAuthor(s.author)).catch(() => {}) }, [])

  const save = useCallback(async () => {
    const { title, body, notes, summary, sourceUrl } = latest.current
    setSaved('saving')
    try {
      await api.put(`/api/drafts/${id}`, { title, body, notes, summary, source_url: sourceUrl.trim() })
      setDraft(d => d && { ...d, title, body, notes, summary, source_url: sourceUrl.trim() })
      setSaved('saved'); setSavedAt(Date.now() / 1000)
    } catch (e) { toast((e as Error).message, 'error'); setSaved('dirty') }
  }, [id, toast])

  // autosave
  useEffect(() => {
    if (!loaded.current || !draft) return
    if (title === draft.title && body === draft.body && notes === draft.notes
      && summary === (draft.summary || '') && sourceUrl.trim() === (draft.source_url || '')) return
    setSaved('dirty')
    const t = window.setTimeout(save, 900)
    return () => window.clearTimeout(t)
  }, [title, body, notes, summary, sourceUrl, draft, save])

  async function patchCovers(patch: Partial<Draft>) {
    setDraft(d => d && { ...d, ...patch })
    try { await api.put(`/api/drafts/${id}`, patch) } catch (e) { toast((e as Error).message, 'error') }
  }

  /** Put pictures at the editor's cursor. */
  function insertImages(ids: number[]) {
    const ed = richRef.current
    if (!ids.length || !ed) return
    ed.chain().focus().insertContent(ids.map(n => ({ type: 'image', attrs: { src: `/api/images/${n}` } }))).run()
  }

  async function addFiles(files: File[]): Promise<number[]> {
    const pics = files.filter(f => f.type.startsWith('image/'))
    if (!pics.length) return []
    setUploading(true)
    try {
      const added: DraftImage[] = await uploadImages(id, pics)
      setDraft(d => d && { ...d, images: [...(d.images || []), ...added] })
      return added.map(a => a.id)
    } catch (e) { toast((e as Error).message, 'error'); return [] } finally { setUploading(false) }
  }

  // server preview (rendered 公众号 HTML and voice flags)
  useEffect(() => {
    if (!draft) return
    const t = window.setTimeout(() => {
      api.post<Preview>('/api/preview', { platform: draft.platform, body }).then(setPreview).catch(() => {})
    }, 500)
    return () => window.clearTimeout(t)
  }, [body, draft?.platform])

  function toggleSide() {
    setSideOpen(v => { try { localStorage.setItem(SIDE_KEY, v ? 'closed' : 'open') } catch { /* */ } return !v })
  }
  async function openPublish() {
    if (saved !== 'saved') await save()
    setConfirming(true)
  }
  async function remove() {
    if (!window.confirm('删除这篇稿子？历史版本也会一起删掉。')) return
    try { await api.del(`/api/drafts/${id}`); window.dispatchEvent(new Event('cw-refresh-status')); go('drafts') } catch (e) { toast((e as Error).message, 'error') }
  }

  if (!draft) return <div className="page"><div className="skeleton" style={{ height: 30, width: '50%' }} /></div>

  const isX = draft.platform === 'x'
  const generating = draft.status === 'generating'
  const segs = isX ? body.split(SPLIT) : []
  const lengths = segs.map(xLength)
  const overLimit = lengths.some(n => n > X_LIMIT)
  const flags = preview?.flags || []
  const pubs = draft.publications || []
  const connected = isX ? status?.x_connected : status?.wechat_configured
  const tabs = ((isX ? ['comments', 'notes', 'cases', 'voice', 'log'] : ['preview', 'comments', 'media', 'xhs', 'video', 'cases', 'notes', 'voice', 'log']) as SideTab[])
    .filter(k => k !== 'cases' || status?.case_search)
  const revising = draft.status === 'revising'
  const todo = waiting(threads)
  const openCount = threads.filter(t => t.status === 'open').length
  let revision: RevisionInfo | null = null
  try { revision = draft.last_revision ? JSON.parse(draft.last_revision) : null } catch { revision = null }
  const showRevision = !!revision && !revising && hiddenRevision !== revision.after

  return (
    <div className={'editor' + (sideOpen ? '' : ' no-side')}>
      <div className="editor-main">
        <header className="editor-top">
          <button className="btn ghost icon" aria-label="返回稿件" onClick={() => go('drafts')}><Ic.Back /></button>
          <div className="crumb">
            <PlatformPill platform={draft.platform} />
            <span className="t">{title || '未命名'}</span>
          </div>
          <span className="save-state" aria-live="polite">
            {saved === 'saving' ? <><Ic.Loader size={12} /> 保存中</> : saved === 'dirty' ? '有改动' : savedAt ? <><Ic.Check size={12} /> 已保存</> : `上次编辑 ${ago(draft.updated_at)}`}
          </span>
          <div className="right">
            <button className="btn ghost icon" aria-label="历史版本" title="历史版本" onClick={async () => setVersions(await api.get<Version[]>(`/api/drafts/${id}/versions`))}><Ic.History /></button>
            <button className="btn ghost icon danger" aria-label="删除稿子" title="删除" onClick={remove}><Ic.Trash /></button>
            <button className="btn ghost icon" aria-label={sideOpen ? '收起侧栏' : '展开侧栏'} aria-pressed={sideOpen} title="预览与备注" onClick={toggleSide}><Ic.Eye /></button>
            {todo.length > 0 && !revising && (
              <button className="btn" onClick={revise} title="把等 AI 处理的批注一起发给 AI 改"><Ic.Sparkle size={15} /> 按批注修改 {todo.length}</button>
            )}
            <button className="btn primary" disabled={generating || revising || !body.trim() || overLimit} onClick={openPublish}>
              <Ic.Send size={15} /> {isX ? '发到 X' : '推到草稿箱'}
            </button>
          </div>
        </header>

        <div className="editor-scroll">
          <div className="writing">
            {draft.status === 'failed' && <div className="callout" style={{ marginBottom: 18 }}><Ic.Alert size={15} /><span>起草失败：{draft.error}</span></div>}
            {revising && <div className="callout info revise-note"><Ic.Loader size={15} /><span>AI 正在按你的批注改稿，一般一两分钟，改好会自动刷新。这期间正文先锁住。</span></div>}
            {showRevision && revision && (
              <div className="callout info revise-note">
                <Ic.Sparkle size={15} />
                <span><b>AI 按 {revision.threads} 条批注改好了</b>{revision.summary ? `：${revision.summary}` : ''}。每条的回复在右边“批注”里。</span>
                <span className="acts">
                  <button className="btn ghost sm" onClick={() => setDiffFor(revision!.before)}>看改动</button>
                  <button className="btn ghost sm" onClick={undoRevision}>撤回这一轮</button>
                  <button className="btn ghost sm icon" aria-label="收起" onClick={() => setHiddenRevision(revision!.after)}><Ic.Close size={13} /></button>
                </span>
              </div>
            )}
            {generating ? (
              <div className="gen-wait">
                <div className="row muted"><Ic.Sparkle size={16} /> 正在按你的文风起草，一般一两分钟，好了会自动出现。</div>
                <div className="skeleton" style={{ height: 30, width: '70%', marginTop: 10 }} />
                {[96, 100, 88, 93, 64, 98, 90, 72].map((w, i) => <div key={i} className="skeleton" style={{ width: w + '%' }} />)}
              </div>
            ) : (
              <>
                <AutoTextarea className="title-input" placeholder={isX ? '给自己看的标题' : '标题'} value={title} onChange={e => setTitle(e.target.value)} />
                {isX ? (
                  <ThreadEditor segs={segs} lengths={lengths} onChange={next => setBody(next.join(SEP))} />
                ) : (
                  <>
                    <RichEditor value={body} onChange={setBody} onFiles={addFiles} editorRef={richRef} placeholder="从这里开始写"
                      editable={!revising} anchors={anchors} activeAnchor={activeThread} onAnchorClick={focusThread}
                      onComment={sel => { setPendingSel(sel); setSideOpen(true); setTab('comments') }} />
                    {uploading && <div className="row small muted" style={{ marginTop: 8 }}><Ic.Loader size={13} /> 正在上传图片…</div>}
                    <div className="md-hint">图片直接粘贴或拖进正文。高亮在 X 长文里会变成加粗（X 不支持高亮）。封面在右边“封面·配图”里选。</div>
                  </>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {sideOpen && (
        <aside className="side" aria-label="预览与备注">
          <div className="side-tabs" role="tablist">
            {tabs.map(k => (
              <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
                {k === 'preview' && <><Ic.Eye size={15} /> 预览</>}
                {k === 'comments' && <><Ic.Note size={15} /> 批注{openCount > 0 && <span className="badge accent">{openCount}</span>}</>}
                {k === 'media' && <><Ic.Image size={15} /> 封面·配图</>}
                {k === 'xhs' && <><span className="xhs-mark">红</span> 小红书</>}
                {k === 'video' && <><Ic.Film size={15} /> 视频{draft.video?.status && <Ic.Loader size={12} />}</>}
                {k === 'cases' && <><Ic.Bulb size={15} /> 找案例</>}
                {k === 'notes' && <><Ic.Doc size={15} /> 备注</>}
                {k === 'voice' && <><Ic.Sparkle size={15} /> 文风{flags.length > 0 && <span className="badge">{flags.length}</span>}</>}
                {k === 'log' && <><Ic.Send size={15} /> 记录{pubs.length > 0 && <span className="faint small">{pubs.length}</span>}</>}
              </button>
            ))}
          </div>
          <div className="side-body">
            {tab === 'comments' && (
              <CommentsPanel draftId={id} threads={threads} order={order} pendingSel={pendingSel} onClearSel={() => setPendingSel(null)}
                active={activeThread} onActivate={focusThread} onChanged={loadThreads} onRevise={revise} busy={revising} />
            )}
            {tab === 'xhs' && !isX && <XhsPanel draft={draft} onDraft={setDraft} />}
            {tab === 'video' && !isX && <VideoCard draft={draft} onDraft={setDraft} />}
            {tab === 'cases' && <CasesPanel draftId={id} onUse={line => setNotes(n => (n.trim() ? n.trimEnd() + '\n' : '') + line)} />}
            {tab === 'preview' && !isX && <FeedCard draft={draft} title={title} summary={summary} body={body} />}
            {tab === 'media' && !isX && (
              <MediaPanel draft={draft} body={body} summary={summary} sourceUrl={sourceUrl} onSummary={setSummary} onSourceUrl={setSourceUrl}
                onCovers={patchCovers} onInsert={n => insertImages([n])}
                onUploaded={added => setDraft(d => d && { ...d, images: [...(d.images || []), ...added] })}
                onRemoved={n => setDraft(d => d && { ...d, images: (d.images || []).filter(i => i.id !== n),
                  cover_wechat: d.cover_wechat === n ? null : d.cover_wechat, cover_x: d.cover_x === n ? null : d.cover_x })} />
            )}
            {tab === 'preview' && !isX && (
              <div className="phone">
                <div className="phone-bar">公众号文章预览</div>
                <div className="phone-body">
                  <h1>{title || '标题'}</h1>
                  {author && <div className="by">{author}</div>}
                  <div dangerouslySetInnerHTML={{ __html: preview?.html || '' }} />
                </div>
              </div>
            )}
            {tab === 'notes' && (
              <div className="stack">
                <div className="small muted">AI 起草时留的提醒，和你自己想记的，都不会发出去。</div>
                <AutoTextarea className="textarea notes-input" value={notes} onChange={e => setNotes(e.target.value)} placeholder="要核实的事实、待补的话……" />
              </div>
            )}
            {tab === 'voice' && (
              flags.length === 0 ? (
                <div className="empty"><div className="empty-icon"><Ic.Check /></div><b>没有明显的 AI 腔</b><div className="small">这是按你过去 70 多篇公众号算出来的习惯，只是参考。</div></div>
              ) : (
                <div className="stack">
                  <div className="small muted">跟你平时的写法比，下面几处偏离比较大。只是提醒，回去重读这几处就行，不用为了过检查硬改。</div>
                  <ul className="flag-list">{flags.map(f => <li key={f}><Ic.Alert size={14} /><span>{f}</span></li>)}</ul>
                </div>
              )
            )}
            {tab === 'log' && <FeishuLine draft={draft} onChange={() => api.get<Draft>(`/api/drafts/${id}`).then(setDraft)} />}
            {tab === 'log' && (pubs.length === 0 ? (
              <div className="empty"><div className="empty-icon"><Ic.Send /></div><b>还没发过</b><div className="small">{isX ? '点右上角“发到 X”。' : '点右上角“推到草稿箱”，选公众号或 X 长文，之后在平台上检查再发表。'}</div></div>
            ) : (
              <div>{pubs.map(p => <PubLine key={p.id} p={p} />)}</div>
            ))}
          </div>
        </aside>
      )}

      {isX ? (
        <ConfirmPublish open={confirming} draft={{ ...draft, title, body }} segs={segs} connected={!!connected}
          cover={!!status?.wechat_cover} onClose={() => setConfirming(false)}
          onSent={() => { setConfirming(false); setTab('log'); setSideOpen(true); api.get<Draft>(`/api/drafts/${id}`).then(setDraft); window.dispatchEvent(new Event('cw-refresh-status')) }} />
      ) : (
        <PublishLongform open={confirming} draft={{ ...draft, title, body, summary, source_url: sourceUrl.trim() }} body={body} status={status}
          onClose={() => setConfirming(false)}
          onSent={() => { setConfirming(false); setTab('log'); setSideOpen(true); api.get<Draft>(`/api/drafts/${id}`).then(setDraft); window.dispatchEvent(new Event('cw-refresh-status')) }} />
      )}

      <DiffDialog open={diffFor !== null} draftId={id} before={diffFor} onClose={() => setDiffFor(null)} />

      <Dialog open={!!versions} onClose={() => setVersions(null)} title="历史版本">
        {versions && versions.length === 0 ? <div className="muted small" style={{ paddingBottom: 12 }}>还没有历史版本。</div> : (
          <div className="card list-rows" style={{ marginBottom: 6 }}>
            {(versions || []).map(v => (
              <div key={v.id} className="list-row" style={{ cursor: 'default' }}>
                <span className="src-icon">{['generated', 'ai-draft', 'ai-revise'].includes(v.origin) ? <Ic.Sparkle size={15} /> : <Ic.Note size={15} />}</span>
                <div className="grow">
                  <div className="t">{({ generated: 'AI 底稿', 'ai-draft': 'AI 底稿', 'ai-revise': 'AI 按批注改的', 'before-ai': '发批注前你的版本', 'undo-ai': '撤回前的 AI 版本' } as Record<string, string>)[v.origin] || '你的修改'} · {ago(v.created_at)}</div>
                  <div className="sub">{v.body.slice(0, 80)}</div>
                </div>
                <button className="btn sm" onClick={() => { setTitle(v.title); setBody(draft.platform === 'x' ? normalizeThread(v.body) : v.body); setVersions(null); toast('已恢复这个版本') }}>恢复</button>
              </div>
            ))}
          </div>
        )}
      </Dialog>
    </div>
  )
}

function ThreadEditor({ segs, lengths, onChange }: { segs: string[]; lengths: number[]; onChange: (next: string[]) => void }) {
  return (
    <div className="thread">
      {segs.map((s, i) => {
        const n = lengths[i]
        const over = n > X_LIMIT
        return (
          <div key={i} className="tweet">
            <div className="rail"><span className="avatar">{i + 1}</span><span className="line" /></div>
            <div className={'tweet-box' + (over ? ' over' : '')}>
              <AutoTextarea value={s} placeholder={i === 0 ? '第一条，一句话点题' : '接着写'} aria-label={`第 ${i + 1} 条`}
                onChange={e => { const next = [...segs]; next[i] = e.target.value; onChange(next) }} />
              <div className="tweet-foot">
                {segs.length > 1 && (
                  <button className="btn ghost sm" onClick={() => onChange(segs.filter((_, j) => j !== i))} aria-label={`删掉第 ${i + 1} 条`}><Ic.Trash size={13} /></button>
                )}
                <span className={'gauge' + (over ? ' over' : '')}>
                  <Gauge value={n} />{over ? `超了 ${n - X_LIMIT}` : `${n}/${X_LIMIT}`}
                </span>
              </div>
            </div>
          </div>
        )
      })}
      <button className="btn ghost sm add-tweet" onClick={() => onChange([...segs, ''])}><Ic.Plus size={14} /> 加一条</button>
      <div className="md-hint" style={{ marginLeft: 48 }}>中文一个字按 2 算，链接固定按 23 算。带链接的推文按官方计费会贵很多。</div>
    </div>
  )
}

function Gauge({ value }: { value: number }) {
  const r = 8, c = 2 * Math.PI * r
  const pct = Math.min(value / X_LIMIT, 1)
  const color = value > X_LIMIT ? 'var(--danger)' : value > X_LIMIT * 0.9 ? 'var(--warn)' : 'var(--accent)'
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r={r} fill="none" stroke="var(--border)" strokeWidth="2.2" />
      <circle cx="10" cy="10" r={r} fill="none" stroke={color} strokeWidth="2.2" strokeDasharray={c} strokeDashoffset={c * (1 - pct)}
        strokeLinecap="round" transform="rotate(-90 10 10)" />
    </svg>
  )
}

function PubLine({ p }: { p: Publication }) {
  let info: Record<string, unknown> = {}
  try { info = JSON.parse(p.detail || '{}') } catch { /* ignore */ }
  const ok = p.state === 'sent'
  const where = p.platform === 'x' ? 'X' : p.platform === 'x_article' ? 'X 长文草稿' : p.platform === 'zsxq' ? '星球草稿箱' : '公众号草稿箱'
  const label = ok ? (p.platform === 'x' ? '已发出' : `已进${where}`) : p.state === 'unknown' ? `${where}：结果不确定，去平台上看一眼` : `${where}：失败`
  return (
    <div className="pub-item">
      <span className={'pill ' + (ok ? 'ok' : p.state === 'unknown' ? 'warn' : 'danger')}>{label}</span>
      <span className="muted">{ago(p.created_at)}</span>
      {p.platform === 'x' && p.remote_id && <a href={`https://x.com/i/status/${p.remote_id.split(',')[0]}`} target="_blank" rel="noreferrer">看推文 <Ic.External size={12} /></a>}
      {p.platform === 'x_article' && ok && <a href="https://x.com/compose/articles" target="_blank" rel="noreferrer">去 X 看草稿 <Ic.External size={12} /></a>}
      {p.platform === 'zsxq' && ok && typeof info.url === 'string' && <a href={info.url} target="_blank" rel="noreferrer">去星球看草稿 <Ic.External size={12} /></a>}
      {typeof info.error === 'string' && <div className="err" style={{ width: '100%' }}>{info.error}</div>}
    </div>
  )
}

function ConfirmPublish({ open, draft, segs, connected, cover, onClose, onSent }: {
  open: boolean; draft: Draft; segs: string[]; connected: boolean; cover: boolean; onClose: () => void; onSent: () => void
}) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const isX = draft.platform === 'x'
  const posts = segs.filter(s => s.trim())
  const links = posts.filter(t => /https?:\/\//.test(t)).length
  const cost = (posts.length - links) * 0.015 + links * 0.2

  useEffect(() => { if (open) setErr('') }, [open])

  async function send() {
    setBusy(true); setErr('')
    try {
      await api.post(`/api/drafts/${draft.id}/publish`, { confirm: true })
      toast(isX ? '已发到 X' : '已推到公众号草稿箱，去手机上点发表')
      onSent()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  if (!connected) {
    return (
      <Dialog open={open} onClose={onClose} title={isX ? 'X 还没连接' : '公众号还没配好'}
        footer={<><button className="btn" onClick={onClose}>关闭</button><button className="btn primary" onClick={() => go('settings')}>去设置</button></>}>
        <p className="muted" style={{ marginTop: 0 }}>{isX ? '先在设置里连接 X 账号，再回来发。' : '先在设置里填好公众号的 AppSecret，再回来推送。'}稿子已经自动保存，不会丢。</p>
      </Dialog>
    )
  }

  return (
    <Dialog open={open} onClose={onClose} title={isX ? `发到 X（${posts.length} 条）` : '推到公众号草稿箱'} wide
      footer={<>
        <button className="btn" onClick={onClose} disabled={busy}>再改改</button>
        <button className="btn primary" onClick={send} disabled={busy}>{busy ? <Ic.Loader size={15} /> : <Ic.Send size={15} />} {isX ? '确认发出' : '确认推送'}</button>
      </>}>
      {isX ? (
        <div className="stack">
          <div className="callout info"><Ic.Alert size={15} /><span>会用 @ 你连接的账号公开发出，第一条之后的按回复串起来。按官方计费约 ${cost.toFixed(3)}。发出去要删只能到 X 上删。</span></div>
          <div className="preview-box">{posts.map((t, i) => `${i + 1}/ ${t}`).join('\n\n')}</div>
        </div>
      ) : (
        <div className="stack">
          <div className="callout info"><Ic.Alert size={15} /><span>只是放进草稿箱，不会群发。之后在手机“公众号助手”或电脑后台检查一遍再点发表。{cover ? '封面用默认封面。' : '还没有默认封面，推送前要先在设置里上传一张。'}</span></div>
          <div className="preview-box"><b>{draft.title}</b>{'\n\n'}{draft.body.slice(0, 500)}{draft.body.length > 500 ? '\n……' : ''}</div>
        </div>
      )}
      {err && <div className="err" style={{ marginTop: 10 }}>{err}</div>}
    </Dialog>
  )
}
