import { useCallback, useEffect, useRef, useState } from 'react'
import { api, pubLabel, renderOf, X_LIMIT, xWeighted, type Draft, type Status, type VideoFormat, type VideoMeta, type VideoPreview, type VideoScene, type VideoSend } from '../api'
import { go } from '../App'
import { ago, AutoTextarea, Dialog, Empty, useToast } from '../ui'
import { Field, fmtDur, fmtSize, RenderProgress, SceneEditor, speakSec, TtsWarning, TYPE_HINT, TYPE_LABEL, TYPES, useVideo } from './Video'
import * as Ic from '../icons'

type Frame = Window & { cwSeek?: (t: number) => void; cwPoster?: number }
const fmtSec = (t: number) => t.toFixed(1)

/** One scene played in the browser, without rendering: the server builds the
 * scene's page (same composition, captions and author mark as the render) and
 * this drives its timeline from the voice. Edits rebuild it a moment after you
 * stop typing; a changed line plays silent on estimated timing until you ask
 * for the voice, so typing never calls the TTS. */
function ScenePreview({ draftId, index, scene, last, tts, format, style }: {
  draftId: number; index: number; scene: VideoScene; last: boolean; tts: boolean; format: VideoFormat; style: string
}) {
  const [p, setP] = useState<VideoPreview | null>(null)
  const [busy, setBusy] = useState<'' | 'build' | 'speak'>('')
  const [err, setErr] = useState('')
  const [playing, setPlaying] = useState(false)
  const [t, setT] = useState(0)
  const frame = useRef<HTMLIFrameElement>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const clock = useRef(0)
  const raf = useRef(0)
  const pos = useRef(0)
  const seq = useRef(0)
  const autoplay = useRef(false)
  const lastIndex = useRef(index)

  const seek = (time: number) => {
    (frame.current?.contentWindow as Frame | null)?.cwSeek?.(time)
    setT(time)
  }
  const stop = useCallback(() => {
    cancelAnimationFrame(raf.current)
    audio.current?.pause()
    audio.current = null
    setPlaying(false)
  }, [])
  useEffect(() => stop, [stop])

  async function build(speak: boolean) {
    const n = ++seq.current
    if (!scene.say.trim()) { setP(null); setErr('这个镜头还没写口播，写了才能预览'); return }
    setBusy(speak ? 'speak' : 'build')
    try {
      const r = await api.post<VideoPreview>(`/api/drafts/${draftId}/video/preview`, { index, scene, last, speak, format })
      if (n !== seq.current) return
      setErr('')
      autoplay.current = speak
      pos.current = 0
      setP(r)
    } catch (e) {
      if (n === seq.current) setErr((e as Error).message)
    } finally {
      if (n === seq.current) setBusy('')
    }
  }

  // rebuild when the scene changes: at once for another scene, after a pause while typing
  const key = JSON.stringify([index, last, scene, format, style])
  useEffect(() => {
    stop()
    const wait = lastIndex.current === index && p ? 600 : 0
    lastIndex.current = index
    const timer = window.setTimeout(() => { void build(false) }, wait)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  function finish() {
    stop()
    pos.current = 0
    seek((frame.current?.contentWindow as Frame | null)?.cwPoster ?? 0)
  }
  function play() {
    if (!p) return
    stop()
    const from = pos.current < p.duration - 0.05 ? pos.current : 0
    if (p.audio) {
      const a = new Audio(p.audio)
      a.currentTime = from
      a.play().catch(() => stop())
      audio.current = a
    } else {
      clock.current = performance.now() - from * 1000
    }
    setPlaying(true)
    const loop = () => {
      const time = audio.current ? audio.current.currentTime : (performance.now() - clock.current) / 1000
      if (time >= p.duration || (audio.current && audio.current.ended)) { finish(); return }
      pos.current = time
      seek(time)
      raf.current = requestAnimationFrame(loop)
    }
    raf.current = requestAnimationFrame(loop)
  }
  function pause() {
    stop()
  }
  function scrub(e: React.PointerEvent<HTMLDivElement>) {
    if (!p) return
    const r = e.currentTarget.getBoundingClientRect()
    const at = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * p.duration
    const was = playing
    stop()
    pos.current = at
    seek(at)
    if (was) play()
  }
  function loaded() {
    const w = frame.current?.contentWindow as Frame | null
    setT(w?.cwPoster ?? 0)
    if (autoplay.current) { autoplay.current = false; play() }
  }

  return (
    <div className="spv">
      <div className={'spv-frame' + (format === 'landscape' ? ' wide' : '')}>
        {p ? <iframe ref={frame} src={p.url} title="镜头预览" onLoad={loaded} />
          : <div className="spv-empty small faint">{err || '正在准备预览…'}</div>}
        {busy && <span className="spv-busy"><Ic.Loader size={12} /> {busy === 'speak' ? '正在配音' : '更新中'}</span>}
      </div>
      {p && (
        <>
          <div className="spv-controls">
            <button className="btn icon sm" onClick={playing ? pause : play} aria-label={playing ? '暂停' : '播放这个镜头'}>
              {playing ? <Ic.Pause size={15} /> : <Ic.Play size={14} />}
            </button>
            <div className="spv-bar" onPointerDown={scrub} role="slider" aria-label="播放位置" aria-valuemin={0}
              aria-valuemax={p.duration} aria-valuenow={Math.round(t * 10) / 10}>
              <span><i style={{ width: `${Math.min(100, (t / p.duration) * 100)}%` }} /></span>
            </div>
            <span className="small faint spv-time">{fmtSec(t)} / {fmtSec(p.duration)} 秒</span>
          </div>
          {err && <div className="err small">{err}</div>}
          {!p.voiced && (
            <div className="spv-note small">
              <span>这句还没配音，现在是按估算语速的静音预览。</span>
              {tts && <button className="btn sm" disabled={!!busy} onClick={() => build(true)}><Ic.Mic size={13} /> 配音试听</button>}
            </div>
          )}
        </>
      )}
    </div>
  )
}

/** Comma-separated key words; the text is kept as typed so a trailing comma survives. */
function KeysInput({ keys, onChange }: { keys: string[]; onChange: (keys: string[]) => void }) {
  const [text, setText] = useState(keys.join('，'))
  const joined = keys.join('，')
  useEffect(() => {
    setText(prev => (prev.split(/[,，]/).map(x => x.trim()).filter(Boolean).join('，') === joined ? prev : joined))
  }, [joined])
  return (
    <input className="input" value={text} placeholder="如 三天，0 行代码"
      onChange={e => { setText(e.target.value); onChange(e.target.value.split(/[,，]/).map(x => x.trim()).filter(Boolean).slice(0, 3)) }} />
  )
}

/** The standing headline some styles keep on top: two short lines, one word in yellow. */
function HeadlineEditor({ lines, keyWord, title, onSave }: { lines: string[]; keyWord: string; title: string; onSave: (lines: string[], key: string) => void }) {
  const [a, setA] = useState(lines[0] || '')
  const [b, setB] = useState(lines[1] || '')
  const [k, setK] = useState(keyWord)
  useEffect(() => { setA(lines[0] || ''); setB(lines[1] || ''); setK(keyWord) }, [lines, keyWord])
  const save = () => onSave([a, b].map(x => x.trim()).filter(Boolean), k.trim())
  return (
    <div className="studio-headline">
      <div className="small muted">常驻标题<em className="faint"> 一直挂在画面顶部，每行 10 个字以内</em></div>
      <input className="input sm" value={a} maxLength={14} placeholder={title.slice(0, 12) || '第一行：是什么事'} onChange={e => setA(e.target.value)} onBlur={save} aria-label="常驻标题第一行" />
      <input className="input sm" value={b} maxLength={14} placeholder="第二行：钩子或结论" onChange={e => setB(e.target.value)} onBlur={save} aria-label="常驻标题第二行" />
      <input className="input sm" value={k} maxLength={10} placeholder="第一行里标黄的词（可选）" onChange={e => setK(e.target.value)} onBlur={save} aria-label="标黄的词" />
    </div>
  )
}

/** Full-screen workspace for the video of one long-form draft: the scenes on the
 * left, the chosen scene in the middle, and on the right an instant preview of
 * that scene or the rendered video, which jumps to whichever scene you pick. */
type VideoHook = ReturnType<typeof useVideo>
const hashOf = (text: string) => { let h = 0; for (const ch of text) h = (h * 31 + (ch.codePointAt(0) || 0)) | 0; return (h >>> 0).toString(36) }

/** The cover as the server draws it, in the video's style. It is drawn again a
 * moment after the cover words stop changing; the old one stays until then. */
function CoverPreview({ draftId, fmt, meta, style }: { draftId: number; fmt: VideoFormat; meta: VideoMeta; style: string }) {
  const key = hashOf(JSON.stringify([meta.cover, style, fmt]))
  const [src, setSrc] = useState('')
  const [shown, setShown] = useState('')
  const [err, setErr] = useState(false)
  useEffect(() => {
    const t = window.setTimeout(() => setSrc(`/api/drafts/${draftId}/video/cover.jpg?format=${fmt}&v=${key}`), shown ? 600 : 0)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId, fmt, key])
  useEffect(() => {
    if (!src) return
    const im = new Image()
    im.onload = () => { setShown(src); setErr(false) }
    im.onerror = () => setErr(true)
    im.src = src
  }, [src])
  return (
    <div className="spv">
      <div className={'spv-frame cover' + (fmt === 'landscape' ? ' wide' : '')}>
        {shown && <img src={shown} alt="封面" />}
        {!shown && !err && <div className="spv-empty"><Ic.Loader size={18} /></div>}
        {err && <div className="spv-empty small muted">封面没画出来，改一下封面字再试</div>}
        {shown && src !== shown && !err && <span className="spv-busy small"><Ic.Loader size={12} /> 更新中</span>}
      </div>
      <div className="row">
        <a className="btn sm" href={`/api/drafts/${draftId}/video/cover.jpg?format=${fmt}&download=1`}><Ic.Download size={14} /> 下载{fmt === 'vertical' ? '竖屏' : '横屏'}封面</a>
      </div>
      <div className="small faint">
        {fmt === 'vertical'
          ? '竖屏封面跟着竖屏片发到 X；手动发视频号、抖音、小红书时也能用，中间 3:4 的部分不会被裁掉字。'
          : '横屏封面跟着横屏片发到 X，也是公众号图文草稿的封面，裁成 2.35:1 和 1:1 时大字都在里面。'}
      </div>
    </div>
  )
}

/** Title, intro and cover words: the AI writes them after the script, the author edits them here. */
function PublishEditor({ draft, v, fmt, status }: { draft: Draft; v: VideoHook; fmt: VideoFormat; status: Status | null }) {
  const { s } = v
  const meta = s.meta
  const generating = s.meta_status === 'generating'
  const images = (draft.images || []).filter(im => !im.name.startsWith('小红书-'))
  const lines = meta ? [meta.cover.lines[0] || '', meta.cover.lines[1] || ''] : ['', '']
  const setLine = (i: number, text: string) => v.setMeta({ cover: { lines: lines.map((x, j) => (j === i ? text : x)) } })
  const keyMissing = !!meta?.cover.key && !lines.some(x => x.includes(meta.cover.key))
  return (
    <div className="studio-edit-inner">
      <div className="studio-edit-head">
        <h2 className="studio-h">发布</h2>
        {meta && (
          <button className="btn ghost sm" style={{ marginLeft: 'auto' }} disabled={generating} onClick={v.writeMeta}>
            {generating ? <Ic.Loader size={13} /> : <Ic.Sparkle size={13} />} 重新生成
          </button>
        )}
      </div>
      <div className="small faint">标题、简介和封面会和成片一起推出去。AI 先写一版，你在这里改，改完自动保存。</div>
      {s.meta_error && !generating && <div className="err small">{s.meta_error}</div>}
      {!meta ? (
        generating ? (
          <div className="stack">
            <div className="row muted small"><Ic.Loader size={14} /> 正在按文章和脚本写标题、简介和封面字，半分钟左右。</div>
            <div className="skeleton" style={{ height: 40 }} /><div className="skeleton" style={{ height: 96 }} />
          </div>
        ) : (
          <div className="card card-pad stack">
            <div className="small muted">还没有标题、简介和封面。AI 会看一遍长文和视频脚本，写一个标题、一段口语化的简介，再给封面配两行大字。</div>
            <div><button className="btn primary sm" onClick={v.writeMeta}><Ic.Sparkle size={14} /> 生成标题、简介和封面</button></div>
          </div>
        )
      ) : (
        <>
          <Field label="标题" hint={`${[...meta.title].length}/30`}>
            <input className="input" maxLength={30} value={meta.title} onChange={e => v.setMeta({ title: e.target.value })} />
          </Field>
          <Field label="简介" hint={`${[...meta.intro].length}/160 · 口语一点，像你发视频时随手写的`}>
            <AutoTextarea className="textarea" maxLength={160} value={meta.intro} onChange={e => v.setMeta({ intro: e.target.value })} />
          </Field>
          <div className="pub-grid">
            <Field label="封面大字 · 第一行"><input className="input" maxLength={12} value={lines[0]} onChange={e => setLine(0, e.target.value)} /></Field>
            <Field label="第二行" hint="可以空着"><input className="input" maxLength={12} value={lines[1]} onChange={e => setLine(1, e.target.value)} /></Field>
            <Field label="标亮的词" hint={keyMissing ? '不在大字里，不会标亮' : '要原样出现在大字里'}>
              <input className="input" maxLength={8} value={meta.cover.key} onChange={e => v.setMeta({ cover: { key: e.target.value } })} />
            </Field>
            <Field label="大字上面的小标签" hint="可以空着">
              <input className="input" maxLength={10} value={meta.cover.kicker} onChange={e => v.setMeta({ cover: { kicker: e.target.value } })} />
            </Field>
          </div>
          <Field label="封面上的截图">
            <select className="input" value={meta.cover.image ?? ''} onChange={e => v.setMeta({ cover: { image: e.target.value ? Number(e.target.value) : null } })}>
              <option value="">不放截图，只放大字</option>
              {images.map(im => <option key={im.id} value={im.id}>{im.name}</option>)}
            </select>
          </Field>
        </>
      )}
      <SendBox draft={draft} v={v} fmt={fmt} status={status} />
    </div>
  )
}

function SendLine({ r }: { r: VideoSend }) {
  let d: Record<string, unknown> = {}
  try { d = JSON.parse(r.detail || '{}') } catch { /* ignore */ }
  const ok = r.state === 'sent'
  return (
    <div className="pub-item">
      <span className={'pill ' + (ok ? 'ok' : r.state === 'pending' ? 'accent' : r.state === 'unknown' ? 'warn' : 'danger')}>
        {r.state === 'pending' ? <><Ic.Loader size={12} /> {r.stage || '发送中'}{r.pct ? ` ${r.pct}%` : ''}</> : pubLabel(r)}
      </span>
      <span className="muted">{d.format === 'landscape' ? '横屏' : '竖屏'} · {ago(r.created_at)}</span>
      {r.platform === 'x_video' && ok && r.remote_id && <a href={`https://x.com/i/status/${r.remote_id}`} target="_blank" rel="noreferrer">看帖子 <Ic.External size={12} /></a>}
      {r.platform === 'wechat_video' && ok && <a href="https://mp.weixin.qq.com/" target="_blank" rel="noreferrer">去公众号后台 <Ic.External size={12} /></a>}
      {typeof d.cover_error === 'string' && <div className="small faint" style={{ width: '100%' }}>{d.cover_error}，封面用的是 X 自己截的画面。</div>}
      {typeof d.error === 'string' && <div className="err" style={{ width: '100%' }}>{d.error}</div>}
    </div>
  )
}

/** Sending the render of the chosen format: a post on X, or the 公众号 (素材库 + a draft). */
function SendBox({ draft, v, fmt, status }: { draft: Draft; v: VideoHook; fmt: VideoFormat; status: Status | null }) {
  const toast = useToast()
  const [rows, setRows] = useState<VideoSend[]>([])
  const [open, setOpen] = useState<'' | 'x' | 'wechat'>('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const watching = useRef(new Set<number>())
  const load = useCallback(async () => {
    const r = await api.get<VideoSend[]>(`/api/drafts/${draft.id}/video/publications`).catch(() => null)
    if (!r) return
    for (const row of r) {
      if (!watching.current.has(row.id) || row.state === 'pending') continue
      watching.current.delete(row.id)
      if (row.state === 'sent') toast(row.platform === 'x_video' ? '视频已经发到 X 了' : '推好了：视频在素材库，图文草稿在草稿箱')
      else toast(pubLabel(row), 'error')
    }
    setRows(r)
  }, [draft.id, toast])
  useEffect(() => { void load() }, [load])
  const pending = rows.some(r => r.state === 'pending')
  useEffect(() => {
    if (!pending) return
    const t = window.setInterval(load, 1500)
    return () => window.clearInterval(t)
  }, [pending, load])

  const rendered = renderOf(v.s, fmt)
  const name = fmt === 'vertical' ? '竖屏' : '横屏'
  const meta = v.s.meta
  const busyOn = (platform: string) => rows.some(r => r.platform === platform && r.state === 'pending')
  const n = xWeighted(text)

  function openX() {
    const title = meta?.title.trim() || ''
    const intro = meta?.intro.trim() || ''
    const both = title && intro ? `${title}\n\n${intro}` : title || intro
    setText(xWeighted(both) <= X_LIMIT ? both : intro)
    setErr('')
    setOpen('x')
  }
  async function send(target: 'x' | 'wechat') {
    setBusy(true)
    setErr('')
    try {
      await v.flush()
      const row = await api.post<VideoSend>(`/api/drafts/${draft.id}/video/publish`, { target, format: fmt, text: target === 'x' ? text : '', confirm: true })
      watching.current.add(row.id)
      setRows(r => [row, ...r])
      setOpen('')
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const xLast = rows.find(r => r.platform === 'x_video')
  const wxLast = rows.find(r => r.platform === 'wechat_video')
  return (
    <div className="vsend">
      <div className="studio-edit-head"><h2 className="studio-h">推出去</h2><span className="small faint">用的是{name}成片，换画幅在右上角</span></div>
      {!rendered && <div className="vnote warn small"><Ic.Alert size={14} /> 还没出{name}片，先点右上角“出{name}片”。</div>}
      {rendered && v.staleOf(fmt) && <div className="vnote small"><Ic.Alert size={13} /> 脚本或风格改过了，现在推的是改之前的{name}片。</div>}
      <div className="row">
        <button className="btn sm" disabled={!rendered || busyOn('x_video')} onClick={openX}><Ic.XLogo size={13} /> 发到 X</button>
        <button className="btn sm" disabled={!rendered || busyOn('wechat_video')} onClick={() => { setErr(''); setOpen('wechat') }}><Ic.WechatLogo size={14} /> 推到公众号</button>
      </div>
      {xLast && <SendLine r={xLast} />}
      {wxLast && <SendLine r={wxLast} />}

      <Dialog open={open === 'x'} onClose={() => setOpen('')} title={`发到 X（${name}片）`} wide
        footer={status?.x_connected ? <>
          <button className="btn" onClick={() => setOpen('')} disabled={busy}>再想想</button>
          <button className="btn primary" onClick={() => send('x')} disabled={busy || n > X_LIMIT}>{busy ? <Ic.Loader size={15} /> : <Ic.Send size={15} />} 确认发出</button>
        </> : <><button className="btn" onClick={() => setOpen('')}>关闭</button><button className="btn primary" onClick={() => go('settings')}>去设置连接 X</button></>}>
        {status?.x_connected ? (
          <div className="stack">
            <div className="callout info"><Ic.Alert size={15} /><span>会用你连接的 X 账号马上公开发出，X 的帖子没有草稿。带上{name}成片{meta ? '和封面' : ''}，按官方计费一条约 $0.015。发出去要删只能到 X 上删。</span></div>
            <div className={'tweet-box' + (n > X_LIMIT ? ' over' : '')}>
              <AutoTextarea value={text} onChange={e => setText(e.target.value)} aria-label="帖子文字" />
              <div className="small faint" style={{ textAlign: 'right' }}>{n > X_LIMIT ? `超了 ${n - X_LIMIT}` : `${n}/${X_LIMIT}`}</div>
            </div>
            <div className="small faint">默认是标题加简介，这里改只影响这一条帖子。</div>
          </div>
        ) : <p className="muted" style={{ marginTop: 0 }}>X 还没连接，先去设置里连接账号再回来发。</p>}
        {err && <div className="err" style={{ marginTop: 10 }}>{err}</div>}
      </Dialog>

      <Dialog open={open === 'wechat'} onClose={() => setOpen('')} title={`推到公众号（${name}片）`} wide
        footer={status?.wechat_configured ? <>
          <button className="btn" onClick={() => setOpen('')} disabled={busy}>取消</button>
          <button className="btn primary" onClick={() => send('wechat')} disabled={busy}>{busy ? <Ic.Loader size={15} /> : <Ic.Send size={15} />} 确认推送</button>
        </> : <><button className="btn" onClick={() => setOpen('')}>关闭</button><button className="btn primary" onClick={() => go('settings')}>去设置</button></>}>
        {status?.wechat_configured ? (
          <div className="stack">
            <div className="callout info"><Ic.Alert size={15} /><span>公众号的接口不能把视频直接放进图文，所以分两步：视频传进素材库（超过 10MB 会先压成 720p），同时在草稿箱建一篇图文，标题、{meta ? '横屏封面' : '默认封面'}、简介都放好。不会群发。</span></div>
            <div className="preview-box"><b>{meta?.title || draft.title}</b>{'\n\n'}{meta?.intro || draft.summary || ''}</div>
            <div className="small muted">推好以后，在手机“公众号助手”或电脑后台打开这篇草稿，在开头点「视频」，从素材库选这条视频（新传的要等几分钟转码），检查一遍再发表。</div>
          </div>
        ) : <p className="muted" style={{ marginTop: 0 }}>公众号还没配好，先在设置里填 AppID 和 AppSecret。</p>}
        {err && <div className="err" style={{ marginTop: 10 }}>{err}</div>}
      </Dialog>
    </div>
  )
}

export function VideoStudio({ id, status }: { id: number; status: Status | null }) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [missing, setMissing] = useState(false)
  const [sel, setSel] = useState(0)
  const [mode, setMode] = useState<'scene' | 'video'>('scene')
  const [view, setView] = useState<'edit' | 'publish'>('edit')
  const [fmt, setFmtState] = useState<VideoFormat>(() => {
    try { return localStorage.getItem('cw-video-format') === 'landscape' ? 'landscape' : 'vertical' } catch { return 'vertical' }
  })
  const setFmt = (f: VideoFormat) => { setFmtState(f); try { localStorage.setItem('cw-video-format', f) } catch { /* private mode */ } }
  const [playingScene, setPlayingScene] = useState<number | null>(null)
  const player = useRef<HTMLVideoElement>(null)
  const v = useVideo(draft, setDraft)
  const { s, scenes, opts } = v

  useEffect(() => {
    setDraft(null)
    setMissing(false)
    setSel(0)
    api.get<Draft>(`/api/drafts/${id}`).then(setDraft).catch(() => setMissing(true))
  }, [id])
  useEffect(() => { setSel(0) }, [s.planned_at])
  useEffect(() => { if (scenes.length && sel >= scenes.length) setSel(scenes.length - 1) }, [scenes.length, sel])
  const rendered = renderOf(s, fmt)
  useEffect(() => { if (!rendered) setMode('scene') }, [rendered])

  // the render leaves out scenes with nothing to say, so map list positions onto rendered ones
  const spoken = scenes.map((sc, i) => (sc.say.trim() ? scenes.slice(0, i).filter(x => x.say.trim()).length : -1))
  const startOf = (i: number) => (spoken[i] >= 0 ? rendered?.scenes?.[spoken[i]]?.start : undefined)
  function seekVideo(i: number, play: boolean) {
    const at = startOf(i)
    const el = player.current
    if (at === undefined || !el) return
    el.currentTime = at + 0.05
    if (play) el.play().catch(() => {})
  }
  function onVideoTime() {
    const el = player.current
    if (!el || !rendered) return
    const k = rendered.scenes.findIndex(x => el.currentTime >= x.start && el.currentTime < x.start + x.duration)
    setPlayingScene(k < 0 ? null : spoken.indexOf(k))
  }
  function pick(i: number) {
    setSel(i)
    setView('edit')
    if (mode === 'video') seekVideo(i, true)
  }
  function showVideo() {
    setMode('video')
    window.setTimeout(() => seekVideo(sel, false), 50)
  }
  function switchFormat(f: VideoFormat) {
    setFmt(f)
    if (mode === 'video' && renderOf(s, f)) window.setTimeout(() => seekVideo(sel, false), 80)
  }

  const setScene = (i: number, patch: Partial<VideoScene>) => v.saveScenes(scenes.map((sc, j) => (j === i ? { ...sc, ...patch } : sc)))
  function move(i: number, d: -1 | 1) {
    const j = i + d
    if (j < 0 || j >= scenes.length) return
    const next = [...scenes]
    ;[next[i], next[j]] = [next[j], next[i]]
    v.saveScenes(next)
    setSel(j)
  }
  function remove(i: number) {
    if (!window.confirm(`删掉第 ${i + 1} 个镜头？`)) return
    v.saveScenes(scenes.filter((_, j) => j !== i))
    setSel(Math.max(0, i - 1))
  }
  function add() {
    v.saveScenes([...scenes.slice(0, sel + 1), { type: 'points', say: '', title: '' }, ...scenes.slice(sel + 1)])
    setSel(scenes.length ? sel + 1 : 0)
  }
  async function back(to: string) {
    await v.flush()
    go(to)
  }

  if (missing) return <div className="page narrow"><div className="card"><Empty icon={<Ic.Film />} title="找不到这篇长文">它可能已经删掉了。<button className="btn sm" onClick={() => go('videos')}>回到视频列表</button></Empty></div></div>
  if (!draft) return <div className="studio"><div className="studio-top" /><div className="studio-center"><div className="skeleton" style={{ height: 240, width: '100%', maxWidth: 520 }} /></div></div>
  if (draft.platform !== 'wechat') return <div className="page narrow"><div className="card"><Empty icon={<Ic.Film />} title="只有长文能做成视频">X 推文没有视频版。</Empty></div></div>

  const scene = scenes[sel]
  const images = (draft.images || []).filter(im => !im.name.startsWith('小红书-'))
  const clips = (draft.clips || []).filter(c => c.state === 'ready')
  const canRender = scenes.some(sc => sc.say.trim())

  return (
    <div className={'studio' + (fmt === 'landscape' ? ' wide-fmt' : '')}>
      <header className="studio-top">
        <button className="btn ghost icon" aria-label="回到视频列表" onClick={() => back('videos')}><Ic.Back /></button>
        <div className="crumb">
          <span className="pill"><Ic.Film size={12} /> 视频</span>
          <span className="t">{draft.title || '（还没有标题）'}</span>
        </div>
        <button className="btn ghost sm studio-doc" onClick={() => back(`drafts/${id}`)}><Ic.Doc size={14} /> 长文</button>
        {scenes.length > 0 && (
          <div className="right">
            <div className="segmented" role="group" aria-label="画幅">
              {(['vertical', 'landscape'] as const).map(f => (
                <button key={f} aria-pressed={fmt === f} onClick={() => switchFormat(f)}>{f === 'vertical' ? '竖屏' : '横屏'}</button>
              ))}
            </div>
            {opts && opts.styles.length > 1 && (
              <select className="input sm" value={s.style || opts.styles[0].id} onChange={e => v.setStyle(e.target.value)} aria-label="画面风格"
                title={opts.styles.find(x => x.id === (s.style || opts.styles[0].id))?.hint}>
                {opts.styles.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
            )}
            {opts && (
              <select className="input sm" value={s.voice || opts.default_voice} onChange={e => v.setVoice(e.target.value)} aria-label="配音音色">
                {opts.voices.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
            )}
            {opts?.bgm && <label className="vcheck small"><input type="checkbox" checked={s.bgm !== false} onChange={e => v.setBgm(e.target.checked)} /> 配乐</label>}
            <button className={'btn sm' + (view === 'publish' ? ' primary' : '')} aria-pressed={view === 'publish'}
              onClick={() => setView(view === 'publish' ? 'edit' : 'publish')}>
              <Ic.Send size={14} /> {view === 'publish' ? '回到镜头' : '发布'}
            </button>
            {s.status === 'rendering'
              ? <span className="pill accent"><Ic.Loader size={12} /> {s.rendering === 'landscape' ? '横屏' : '竖屏'}出片中 {s.pct || 0}%</span>
              : <button className="btn primary sm" disabled={!!v.busy || !canRender || s.status === 'planning'} onClick={() => v.render(fmt)}>
                <Ic.Film size={14} /> {rendered ? '重新出' : '出'}{fmt === 'vertical' ? '竖屏' : '横屏'}片
              </button>}
          </div>
        )}
      </header>

      {s.status === 'planning' ? (
        <div className="studio-center">
          <div className="stack" style={{ width: '100%', maxWidth: 520 }}>
            <div className="row muted small"><Ic.Loader size={14} /> 正在看文章里的截图、写分镜脚本，一般两三分钟。</div>
            <div className="skeleton" style={{ height: 96 }} /><div className="skeleton" style={{ height: 96 }} /><div className="skeleton" style={{ height: 96 }} />
          </div>
        </div>
      ) : !scenes.length ? (
        <div className="studio-center">
          <div className="card card-pad stack" style={{ maxWidth: 520 }}>
            <h2 className="studio-h">把《{draft.title || '这篇长文'}》做成竖屏视频</h2>
            <div className="small muted">AI 先看一遍文章和里面的截图，写出分镜脚本：每个镜头说什么、画面上出什么字、用哪张截图、推近到哪一块。你在这里一个镜头一个镜头地改，右边当场预览，满意了点“出片”，在这台电脑上配音、对齐字幕、渲染成 1080×1920 的 MP4。</div>
            <TtsWarning opts={opts} />
            {s.error && <div className="err small">{s.error}</div>}
            {!draft.body.trim() && <div className="vnote warn small"><Ic.Alert size={14} /> 长文还是空的，先去把长文写出来。</div>}
            <div className="row">
              <button className="btn primary" disabled={!draft.body.trim()} onClick={() => v.plan()}><Ic.Sparkle size={14} /> 生成视频脚本</button>
              <button className="btn ghost sm" onClick={add}><Ic.Plus size={13} /> 不用 AI，自己从空白写</button>
            </div>
          </div>
        </div>
      ) : (
        <div className="studio-body">
          <aside className="studio-scenes" aria-label="镜头">
            {opts?.styles.find(x => x.id === (s.style || opts.styles[0].id))?.headline && (
              <HeadlineEditor lines={s.headline || []} keyWord={s.headline_key || ''} title={draft.title}
                onSave={(lines, key) => v.setHeadline(lines, key)} />
            )}
            <div className="studio-scenes-head small"><b>{scenes.length}</b> 个镜头 · 约 {fmtDur(v.total)}</div>
            <ol>
              {scenes.map((sc, i) => (
                <li key={i}>
                  <button type="button" className={'sc-card' + (i === sel ? ' on' : '') + (mode === 'video' && i === playingScene ? ' playing' : '')}
                    aria-current={i === sel} onClick={() => pick(i)}>
                    <span className="sc-top">
                      <span className="vnum">{i + 1}</span>
                      <span className="vtype">{TYPE_LABEL[sc.type]}</span>
                      <span className="faint small vsec">{Math.round(speakSec(sc.say || ''))}″</span>
                    </span>
                    <span className="sc-say">{sc.say || <i className="faint">还没写口播</i>}</span>
                  </button>
                </li>
              ))}
            </ol>
            <div className="studio-scenes-foot">
              <button className="btn ghost sm" onClick={add}><Ic.Plus size={13} /> 加一个镜头</button>
              <button className="btn ghost sm" onClick={v.plan}><Ic.Sparkle size={13} /> 重新生成脚本</button>
            </div>
          </aside>

          <section className="studio-edit" aria-label={view === 'publish' ? '发布' : '编辑镜头'}>
            {view === 'publish' ? <PublishEditor draft={draft} v={v} fmt={fmt} status={status} /> : scene && (
              <div className="studio-edit-inner">
                <div className="studio-edit-head">
                  <h2 className="studio-h">镜头 {sel + 1}</h2>
                  <select className="input sm" value={scene.type} onChange={e => setScene(sel, { type: e.target.value as VideoScene['type'] })} aria-label="镜头类型">
                    {TYPES.map(x => <option key={x} value={x}>{TYPE_LABEL[x]}</option>)}
                  </select>
                  <div className="row" style={{ gap: 2, marginLeft: 'auto' }}>
                    <button type="button" className="btn ghost icon sm" aria-label="往前挪" disabled={sel === 0} onClick={() => move(sel, -1)}><Ic.Up size={14} /></button>
                    <button type="button" className="btn ghost icon sm" aria-label="往后挪" disabled={sel === scenes.length - 1} onClick={() => move(sel, 1)}><Ic.Down size={14} /></button>
                    <button type="button" className="btn ghost icon sm" aria-label="删掉这个镜头" onClick={() => remove(sel)}><Ic.Trash size={14} /></button>
                  </div>
                </div>
                <div className="small faint">{TYPE_HINT[scene.type]}</div>
                <Field label="口播" hint={`这个镜头读出来的话，约 ${Math.round(speakSec(scene.say || ''))} 秒`}>
                  <AutoTextarea className="textarea vsay-input" value={scene.say} onChange={e => setScene(sel, { say: e.target.value })} />
                </Field>
                <Field label="字幕标亮" hint="口播里的 1 到 2 个关键词，逗号隔开；数字会自动标亮">
                  <KeysInput keys={scene.keys || []} onChange={keys => setScene(sel, { keys })} />
                </Field>
                <SceneEditor scene={scene} images={images} clips={clips} set={patch => setScene(sel, patch)} />
                <div className="small faint">口播里的数字和英文名照常写，配音会自动读对；读错的字可以换个说法。画面上的字从口播里提炼，读到哪里出到哪里。</div>
              </div>
            )}
          </section>

          <aside className="studio-preview" aria-label="预览">
            {view === 'publish' ? (s.meta ? <CoverPreview draftId={id} fmt={fmt} meta={s.meta} style={s.style || opts?.styles[0]?.id || 'keynote'} />
              : <div className="small faint" style={{ padding: '24px 4px' }}>封面会在这里出现。</div>) : <>
            <div className="segmented" role="group" aria-label="预览什么">
              <button aria-pressed={mode === 'scene'} onClick={() => setMode('scene')}>这个镜头</button>
              <button aria-pressed={mode === 'video'} disabled={!rendered} onClick={showVideo} title={rendered ? '' : `还没出${fmt === 'vertical' ? '竖屏' : '横屏'}片`}>成片</button>
            </div>
            {mode === 'video' && rendered ? (
              <div className="spv">
                <div className={'spv-frame' + (fmt === 'landscape' ? ' wide' : '')}>
                  <video ref={player} key={v.srcOf(fmt)} src={v.srcOf(fmt)} controls playsInline preload="metadata" onTimeUpdate={onVideoTime} />
                </div>
                <div className="small muted">{fmtDur(rendered.duration)} · {fmt === 'vertical' ? '1080×1920' : '1920×1080'} · {fmtSize(rendered.size)} · 点左边的镜头跳到那一段</div>
                {v.staleOf(fmt) && <div className="vnote small"><Ic.Alert size={13} /> 脚本或风格改过了，这是改之前的成片，重新出片才会更新。</div>}
                <div className="row">
                  <a className="btn sm" href={`/api/drafts/${id}/video.mp4?format=${fmt}&download=1`}><Ic.Download size={14} /> 下载 MP4</a>
                  <button className="btn sm mobile-share" disabled={!!v.busy} onClick={() => v.share(fmt)}>{v.busy === 'share' ? <Ic.Loader size={14} /> : <Ic.Send size={14} />} 分享到手机 App</button>
                </div>
              </div>
            ) : scene ? (
              <ScenePreview draftId={id} index={sel} scene={scene} last={sel === scenes.length - 1} tts={!!opts?.tts}
                format={fmt} style={s.style || opts?.styles[0]?.id || 'clean'} />
            ) : null}
            </>}
            {s.status === 'rendering' && <RenderProgress s={s} />}
            {s.error && !s.status && <div className="err small">{s.error}</div>}
            <TtsWarning opts={opts} />
          </aside>
        </div>
      )}
    </div>
  )
}
