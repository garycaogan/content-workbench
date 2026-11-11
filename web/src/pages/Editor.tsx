import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, DRAFT_FIELDS, pubLabel, type Draft, type DraftClip, type DraftField, type DraftImage, type DraftRevs,
  type Publication, type RevisionInfo, type Status, type Thread } from '../api'
import { go } from '../App'
import { ago, AutoTextarea, Dialog, PlatformPill, useToast } from '../ui'
import * as Ic from '../icons'
import { CasesPanel, FeedCard, FeishuLine, MediaPanel, PublishLongform, uploadClip, uploadImages } from './Longform'
import { RichEditor, type Selection } from '../RichEditor'
import { CommentsPanel, DiffDialog, waiting } from './Comments'
import { XhsPanel } from './Xhs'
import { VideoCard } from './Video'
import { locate, type Anchor } from '../commentAnchors'
import type { Editor as TiptapEditor } from '@tiptap/react'

type Preview = { flags: string[]; thread?: { index: number; text: string; weighted: number; over: boolean }[]; html?: string }
type Version = { id: number; title: string; body: string; created_at: number; origin: string }
type SideTab = 'preview' | 'comments' | 'media' | 'xhs' | 'video' | 'cases' | 'notes' | 'context' | 'voice' | 'log'

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

type Fields = Record<DraftField, string>
/** What the server held when the page last loaded or saved each field, and that version's fingerprint. */
type Base = { values: Fields; rev: DraftRevs }
const FIELD_NAMES: Record<DraftField, string> = { title: '标题', body: '正文', notes: '备注', summary: '摘要', source_url: '阅读原文', context: '背景资料' }
const CHECK_EVERY = 5000
const BUSY = ['generating', 'revising']

/** The draft the way the editor holds it (an X thread is kept normalized). */
function fromServer(d: Draft): Fields {
  return { title: d.title, body: d.platform === 'x' ? normalizeThread(d.body) : d.body, notes: d.notes,
    summary: d.summary || '', source_url: d.source_url || '', context: d.context || '' }
}

function pick<T>(from: Record<DraftField, T>, fields: readonly DraftField[]): Partial<Record<DraftField, T>> {
  return Object.fromEntries(fields.map(f => [f, from[f]]))
}

const names = (fields: readonly DraftField[]) => fields.map(f => FIELD_NAMES[f]).join('、')

const SIDE_KEY = 'cw-editor-side'

export function Editor({ id, status }: { id: number; status: Status | null }) {
  const toast = useToast()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [notes, setNotes] = useState('')
  const [context, setContext] = useState('')
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
  const [clipUpload, setClipUpload] = useState<{ name: string; pct: number } | null>(null)
  const [threads, setThreads] = useState<Thread[]>([])
  const [activeThread, setActiveThread] = useState<number | null>(null)
  const [pendingSel, setPendingSel] = useState<Selection | null>(null)
  const [order, setOrder] = useState<Map<number, number | null>>(new Map())
  const [poll, setPoll] = useState(0)
  const [diffFor, setDiffFor] = useState<number | null>(null)
  const [hiddenRevision, setHiddenRevision] = useState<number | null>(null)
  // fields changed here and elsewhere since the page loaded them: saving stops until the author picks a side
  const [clash, setClash] = useState<DraftField[] | null>(null)
  // after taking the newer version: what the page had and couldn't save, kept on screen
  const [kept, setKept] = useState<{ fields: DraftField[]; mine: Fields; stored: boolean } | null>(null)
  const [base, setBase] = useState<Base | null>(null)
  const baseRef = useRef<Base | null>(null)
  const clashRef = useRef(clash)
  clashRef.current = clash
  const loaded = useRef(false)
  const lastStatus = useRef<string | null>(null)
  const richRef = useRef<TiptapEditor | null>(null)
  const latest = useRef<Fields>({ title, body, notes, summary, source_url: sourceUrl.trim(), context })
  latest.current = { title, body, notes, summary, source_url: sourceUrl.trim(), context }
  const saving = useRef(false)
  const writes = useRef(0)            // saves started; a change check that saw one start meanwhile is dropped
  const queue = useRef<Promise<void>>(Promise.resolve())

  const loadThreads = useCallback(() => api.get<Thread[]>(`/api/drafts/${id}/comments`).then(setThreads).catch(() => {}), [id])

  const updateBase = useCallback((next: (b: Base | null) => Base) => {
    baseRef.current = next(baseRef.current)
    setBase(baseRef.current)
  }, [])

  /** Show the server's version of these fields and edit on from it. */
  const take = useCallback((d: Draft, fields: readonly DraftField[]) => {
    if (!fields.length || !d.rev) return
    const v = fromServer(d)
    const set: Record<DraftField, (s: string) => void> = { title: setTitle, body: setBody, notes: setNotes, summary: setSummary,
      source_url: setSourceUrl, context: setContext }
    for (const f of fields) set[f](v[f])
    updateBase(b => ({ values: { ...(b?.values || v), ...pick(v, fields) }, rev: { ...(b?.rev || d.rev!), ...pick(d.rev!, fields) } }))
  }, [updateBase])

  // load; keep polling while AI is drafting or revising, then take its text
  useEffect(() => {
    if (poll === 0) loaded.current = false
    let t: number | undefined
    const load = () => api.get<Draft>(`/api/drafts/${id}`).then(d => {
      setDraft(d)
      const finishedRevision = lastStatus.current === 'revising' && d.status !== 'revising'
      if (!loaded.current || d.status === 'generating' || finishedRevision) {
        take(d, DRAFT_FIELDS)
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
  }, [id, toast, poll, loadThreads, take])

  /** Take in what changed elsewhere (an AI revision written from outside, another tab). A field the author
   * hasn't touched here is replaced; one changed on both sides waits for the author to choose. */
  const merge = useCallback((d: Draft) => {
    const b = baseRef.current
    if (!b || !d.rev) return
    const mine = latest.current, theirs = fromServer(d)
    const moved = DRAFT_FIELDS.filter(f => d.rev![f] !== b.rev[f])
    const clean = moved.filter(f => mine[f] === b.values[f] || mine[f] === theirs[f])
    const clashing = moved.filter(f => !clean.includes(f))
    setDraft(d)
    take(d, clean)
    if (moved.length) loadThreads()
    setClash(clashing.length ? clashing : null)
    if (!clashing.length && clean.length) toast(`这篇稿子的${names(clean)}在别处改过，已换成最新的`)
  }, [take, loadThreads, toast])

  const checkRemote = useCallback(async () => {
    if (!loaded.current || saving.current || BUSY.includes(lastStatus.current || '')) return
    const started = writes.current
    const fresh = () => writes.current === started && !saving.current
    try {
      const r = await api.get<{ rev: DraftRevs; status: string }>(`/api/drafts/${id}/rev`)
      const b = baseRef.current
      if (!b || !fresh()) return
      if (BUSY.includes(r.status)) { setPoll(n => n + 1); return }  // AI started on it elsewhere: follow it like our own
      if (DRAFT_FIELDS.every(f => r.rev[f] === b.rev[f])) return
      const d = await api.get<Draft>(`/api/drafts/${id}`)
      if (fresh()) merge(d)
    } catch { /* the next check tries again */ }
  }, [id, merge])

  // an open page keeps checking whether the draft changed elsewhere, and checks at once when it comes back into view
  useEffect(() => {
    const check = () => { if (!document.hidden) void checkRemote() }
    const t = window.setInterval(check, CHECK_EVERY)
    window.addEventListener('focus', check)
    document.addEventListener('visibilitychange', check)
    return () => {
      window.clearInterval(t)
      window.removeEventListener('focus', check)
      document.removeEventListener('visibilitychange', check)
    }
  }, [checkRemote])

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
    if (clash) { toast('先处理上面的冲突：这页上的改动还没保存', 'error'); return }
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
      setDraft(d); take(d, ['title', 'body']); toast('已撤回这一轮')
    } catch (e) { toast((e as Error).message, 'error') }
  }

  useEffect(() => { api.get<{ author: string }>('/api/settings').then(s => setAuthor(s.author)).catch(() => {}) }, [])

  // Sends only the fields changed here, each with the version it was edited from; the server refuses (409)
  // a field that changed elsewhere meanwhile instead of overwriting it.
  const saveNow = useCallback(async () => {
    const b = baseRef.current
    if (!b || clashRef.current) return
    const mine = latest.current
    const changed = DRAFT_FIELDS.filter(f => mine[f] !== b.values[f])
    if (!changed.length) return
    saving.current = true
    writes.current += 1
    setSaved('saving')
    let refused = false
    try {
      const r = await api.put<{ rev: DraftRevs }>(`/api/drafts/${id}`, { ...pick(mine, changed), base: pick(b.rev, changed) })
      updateBase(now => ({ values: { ...now!.values, ...pick(mine, changed) }, rev: { ...now!.rev, ...pick(r.rev, changed) } }))
      setSaved('saved'); setSavedAt(Date.now() / 1000)
    } catch (e) {
      setSaved('dirty')
      if (e instanceof ApiError && e.status === 409) { refused = true; setClash(c => c || [...changed]) }
      else toast((e as Error).message, 'error')
    } finally { saving.current = false }
    if (refused) await checkRemote()  // fetch the newer version: fields untouched here come in, the rest wait for a choice
  }, [id, toast, updateBase, checkRemote])

  /** One save at a time, in order; resolves once this one is through. */
  const save = useCallback(() => {
    const run = queue.current.then(saveNow)
    queue.current = run.catch(() => {})
    return run
  }, [saveNow])

  // autosave
  useEffect(() => {
    if (!loaded.current || !draft || !base) return
    const mine = { title, body, notes, summary, source_url: sourceUrl.trim(), context }
    if (DRAFT_FIELDS.every(f => mine[f] === base.values[f])) { setSaved(s => (s === 'dirty' ? 'saved' : s)); return }
    setSaved('dirty')
    if (clash) return
    const t = window.setTimeout(save, 900)
    return () => window.clearTimeout(t)
  }, [title, body, notes, summary, sourceUrl, context, draft, base, clash, save])

  /** Settle a clash by taking the newer version; what the page had stays on screen and, for title and body, in the history. */
  async function takeLatest() {
    try {
      const d = await api.get<Draft>(`/api/drafts/${id}`)
      const b = baseRef.current!, mine = latest.current, theirs = fromServer(d)
      const moved = DRAFT_FIELDS.filter(f => d.rev![f] !== b.rev[f])
      const lost = moved.filter(f => mine[f] !== b.values[f] && mine[f] !== theirs[f])
      const stored = lost.includes('title') || lost.includes('body')
      if (stored) await api.post(`/api/drafts/${id}/versions`, { origin: 'unsaved', title: mine.title, body: mine.body })
      setDraft(d); take(d, moved); setClash(null)
      setKept(lost.length ? { fields: lost, mine, stored } : null)
      loadThreads()
    } catch (e) { toast((e as Error).message, 'error') }
  }

  /** Settle a clash by saving this page's version over the newer one, which stays in the history. */
  async function keepMine() {
    if (!window.confirm('用你这页上的版本覆盖别处的改动？被覆盖的那版会存进历史版本，需要时还能找回来。')) return
    try {
      const d = await api.get<Draft>(`/api/drafts/${id}`)
      const b = baseRef.current!, mine = latest.current
      const moved = DRAFT_FIELDS.filter(f => d.rev![f] !== b.rev[f])
      const ours = moved.filter(f => mine[f] !== b.values[f])
      if (ours.includes('title') || ours.includes('body')) {
        await api.post(`/api/drafts/${id}/versions`, { origin: 'replaced', title: d.title, body: d.body })
      }
      // our fields now count as edited from the newest version, so the next save goes through; the rest come in
      updateBase(now => ({ values: now!.values, rev: { ...now!.rev, ...pick(d.rev!, ours) } }))
      setDraft(d); take(d, moved.filter(f => !ours.includes(f))); setClash(null)
    } catch (e) { toast((e as Error).message, 'error') }
  }

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

  /** Videos go up in pieces, one at a time; the editor inserts them once they are up. */
  async function addClips(files: File[]): Promise<number[]> {
    const ids: number[] = []
    for (const f of files) {
      setClipUpload({ name: f.name, pct: 0 })
      try {
        const c: DraftClip = await uploadClip(id, f, share => setClipUpload({ name: f.name, pct: Math.round(share * 100) }))
        setDraft(d => d && { ...d, clips: [...(d.clips || []), c] })
        ids.push(c.id)
      } catch (e) { toast(`${f.name}：${(e as Error).message}`, 'error') }
    }
    setClipUpload(null)
    return ids
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
    if (clash) { toast('先处理上面的冲突：这页上的改动还没保存', 'error'); return }
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
  const tabs = ((isX ? ['comments', 'notes', 'context', 'cases', 'voice', 'log'] : ['preview', 'comments', 'media', 'xhs', 'video', 'cases', 'notes', 'context', 'voice', 'log']) as SideTab[])
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
            {clash ? <><Ic.Alert size={12} /> 没保存</> : saved === 'saving' ? <><Ic.Loader size={12} /> 保存中</> : saved === 'dirty' ? '有改动' : savedAt ? <><Ic.Check size={12} /> 已保存</> : `上次编辑 ${ago(draft.updated_at)}`}
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
            {clash && (
              <div className="callout revise-note" role="alert">
                <Ic.Alert size={15} />
                <span><b>这篇稿子的{names(clash)}在别处改过了</b>（AI 改稿或别的页面），你这页上也改了还没保存。自动保存先停下，免得两边互相盖掉。你这页上的字都还在。</span>
                <span className="acts">
                  <button className="btn sm primary" onClick={takeLatest}>载入最新版</button>
                  <button className="btn ghost sm" onClick={keepMine}>还是用我这版</button>
                </span>
              </div>
            )}
            {kept && (
              <div className="callout info revise-note kept-note">
                <Ic.Note size={15} />
                <span>已换成最新版。你刚才没保存上的{names(kept.fields)}放在下面{kept.stored ? '，也存进了历史版本「没保存上的版本」' : ''}，要用哪段复制过去。</span>
                <span className="acts"><button className="btn ghost sm icon" aria-label="收起" onClick={() => setKept(null)}><Ic.Close size={13} /></button></span>
                {kept.fields.map(f => (
                  <label key={f} className="kept-field">
                    <span className="small muted">{FIELD_NAMES[f]}</span>
                    <textarea className="textarea" readOnly rows={Math.min(8, kept.mine[f].split('\n').length + 1)} value={kept.mine[f]} />
                  </label>
                ))}
              </div>
            )}
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
                    <RichEditor value={body} onChange={setBody} onFiles={addFiles} onClips={addClips} editorRef={richRef} placeholder="从这里开始写"
                      editable={!revising} anchors={anchors} activeAnchor={activeThread} onAnchorClick={focusThread}
                      onComment={sel => { setPendingSel(sel); setSideOpen(true); setTab('comments') }} />
                    {uploading && <div className="row small muted" style={{ marginTop: 8 }}><Ic.Loader size={13} /> 正在上传图片…</div>}
                    {clipUpload && <div className="row small muted" style={{ marginTop: 8 }}><Ic.Loader size={13} /> 正在上传视频「{clipUpload.name}」{clipUpload.pct}%，传完会出现在光标处，别关这个页面</div>}
                    <div className="md-hint">图片、视频直接粘贴或拖进正文。视频推到 X 长文时能直接播放；公众号和星球的接口放不进视频，那里会换成视频的一帧画面。高亮在 X 长文里会变成加粗（X 不支持高亮）。封面在右边“封面·配图”里选。</div>
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
                {k === 'context' && <><Ic.Book size={15} /> 背景资料</>}
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
            {tab === 'context' && (
              <div className="stack">
                <div className="small muted">写这篇稿子时的事实依据：人物、时间线、数字、链接，哪些能公开。AI 按批注改稿、做小红书版和视频时都会读，只拿来核对事实，不会照搬进正文。它不会发出去，也不存进飞书。</div>
                <AutoTextarea className="textarea notes-input" aria-label="背景资料" value={context} onChange={e => setContext(e.target.value)}
                  placeholder="比如作者的 GitHub 和 X 账号、事情的时间线、实际数据，哪些不能写进正文……" />
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
          onClose={() => setConfirming(false)} onPushed={() => api.get<Draft>(`/api/drafts/${id}`).then(setDraft).catch(() => {})}
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
                  <div className="t">{({ generated: 'AI 底稿', 'ai-draft': 'AI 底稿', 'ai-revise': 'AI 按批注改的', 'before-ai': '发批注前你的版本', 'undo-ai': '撤回前的 AI 版本',
                    unsaved: '没保存上的版本', replaced: '被你覆盖掉的版本' } as Record<string, string>)[v.origin] || '你的修改'} · {ago(v.created_at)}</div>
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
  const label = pubLabel(p)
  return (
    <div className="pub-item">
      <span className={'pill ' + (ok ? 'ok' : p.state === 'unknown' || p.state === 'pending' ? 'warn' : 'danger')}>{label}</span>
      <span className="muted">{ago(p.created_at)}</span>
      {(p.platform === 'x' || p.platform === 'x_video') && p.remote_id && <a href={`https://x.com/i/status/${p.remote_id.split(',')[0]}`} target="_blank" rel="noreferrer">看推文 <Ic.External size={12} /></a>}
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
