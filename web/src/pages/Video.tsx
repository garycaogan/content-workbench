import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type Draft, type DraftImage, type VideoItem, type VideoOptions, type VideoRow, type VideoScene, type VideoState } from '../api'
import { AutoTextarea, useToast } from '../ui'
import { go } from '../App'
import { imgUrl } from './Longform'
import * as Ic from '../icons'

export const TYPES: VideoScene['type'][] = ['hook', 'points', 'shot', 'stat', 'change', 'quote', 'cta']
export const TYPE_LABEL: Record<VideoScene['type'], string> = {
  hook: '开头大字', points: '要点卡片', shot: '截图放大', stat: '数字', change: '前后对比', quote: '金句', cta: '结尾',
}
export const TYPE_HINT: Record<VideoScene['type'], string> = {
  hook: '一两行大字抛出这条视频的看点，可以再跟几张小卡片',
  points: '标题加 1 到 4 张卡片，读到哪条出哪条',
  shot: '真实截图放进窗口，读到某句时推近到框出的区域',
  stat: '一个数字滚动出来，可以再变成另一个数字',
  change: '原来的说法被划掉，改后的说法打出来',
  quote: '一句大字观点，标亮一个词',
  cta: '开源地址或链接、署名，片子在这里收尾',
}

export const speakSec = (say: string) => say.replace(/[\s，。？！、：；,.?!:;“”]/g, '').length / 5.1 + 0.5
export const fmtDur = (sec: number) => (sec >= 60 ? `${Math.floor(sec / 60)} 分 ${Math.round(sec % 60)} 秒` : `${Math.round(sec)} 秒`)
const toLines = (text: string) => text.split('\n').map(x => x.trim()).filter(Boolean)
export const fmtSize = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.round(b / 1e3)} KB`)

/** "文字｜说明" per line ⇄ items; a cue is kept only while the item still reads the same. */
function itemsText(items: VideoItem[] | undefined, withSub: boolean) {
  return (items || []).map(i => (withSub && i.sub ? `${i.text || ''}｜${i.sub}` : i.text || '')).join('\n')
}
function textItems(text: string, old: VideoItem[] | undefined, withSub: boolean): VideoItem[] {
  return toLines(text).map((line, n) => {
    const [t, sub] = withSub ? line.split(/[｜|]/).map(x => x.trim()) : [line, undefined]
    const prev = (old || []).find(o => o.text === t) || (old || [])[n]
    return { text: t, ...(sub ? { sub } : {}), ...(prev && prev.text === t && prev.cue ? { cue: prev.cue } : {}) }
  })
}
function rowsText(rows: VideoRow[] | undefined) {
  return (rows || []).map(r => `${r.label || ''}｜${r.before || ''}｜${r.after || ''}`).join('\n')
}
function textRows(text: string, old: VideoRow[] | undefined): VideoRow[] {
  return toLines(text).map((line, n) => {
    const [label, before, after] = line.split(/[｜|]/).map(x => x.trim())
    const prev = (old || [])[n]
    return { label, before, after: after ?? before, ...(prev?.cue ? { cue: prev.cue } : {}) }
  })
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="vfield">
      <span>{label}{hint && <em>{hint}</em>}</span>
      {children}
    </label>
  )
}

/** Drag on the screenshot to mark the part the camera pushes into. */
function FocusPicker({ image, focus, onChange }: { image: DraftImage; focus?: number[]; onChange: (f: number[] | undefined) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<number[] | null>(null)
  const at = (e: React.PointerEvent) => {
    const r = box.current!.getBoundingClientRect()
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]
  }
  const shown = drag ? [Math.min(drag[0], drag[2]), Math.min(drag[1], drag[3]), Math.max(drag[0], drag[2]), Math.max(drag[1], drag[3])] : focus
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div ref={box} className="vfocus" style={{ aspectRatio: `${image.width} / ${image.height}` }}
        onPointerDown={e => { e.currentTarget.setPointerCapture(e.pointerId); const [x, y] = at(e); setDrag([x, y, x, y]) }}
        onPointerMove={e => { if (drag) { const [x, y] = at(e); setDrag([drag[0], drag[1], x, y]) } }}
        onPointerUp={() => {
          if (drag && shown && shown[2] - shown[0] > 0.03 && shown[3] - shown[1] > 0.03) onChange(shown.map(v => Math.round(v * 10000) / 10000))
          setDrag(null)
        }}>
        <img src={imgUrl(image.id)} alt="" draggable={false} />
        {shown && <div className="vfocus-box" style={{ left: `${shown[0] * 100}%`, top: `${shown[1] * 100}%`, width: `${(shown[2] - shown[0]) * 100}%`, height: `${(shown[3] - shown[1]) * 100}%` }} />}
      </div>
      <div className="row small faint" style={{ justifyContent: 'space-between' }}>
        <span>{shown ? '框里是镜头会推近的地方，拖一下重新框' : '在图上拖出一个框，镜头会推近到那里'}</span>
        {focus && <button type="button" className="btn ghost sm" onClick={() => onChange(undefined)}>看整张图</button>}
      </div>
    </div>
  )
}

function ImagePick({ images, value, onPick }: { images: DraftImage[]; value?: number; onPick: (id: number | undefined) => void }) {
  const [choosing, setChoosing] = useState(false)
  if (!images.length) return <div className="small faint">这篇长文里没有图片</div>
  const picked = images.find(im => im.id === value)
  if (picked && !choosing) return (
    <div className="row" style={{ gap: 8 }}>
      <span className="vthumb on" style={{ width: 72 }}><img src={imgUrl(picked.id)} alt={picked.name} /></span>
      <span className="small faint" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{picked.name}</span>
      <button type="button" className="btn ghost sm" onClick={() => setChoosing(true)}>换一张</button>
    </div>
  )
  return (
    <div className="vthumbs">
      {images.map(im => (
        <button type="button" key={im.id} className={'vthumb' + (im.id === value ? ' on' : '')} onClick={() => { onPick(im.id === value ? undefined : im.id); setChoosing(false) }}
          aria-pressed={im.id === value} title={im.name}>
          <img src={imgUrl(im.id)} alt={im.name} />
        </button>
      ))}
    </div>
  )
}

export function SceneEditor({ scene, images, set }: { scene: VideoScene; images: DraftImage[]; set: (patch: Partial<VideoScene>) => void }) {
  const img = images.find(im => im.id === scene.image)
  const head = (
    <>
      <div className="vgrid2">
        <Field label="小标签" hint="可选"><input className="input" value={scene.eyebrow || ''} placeholder="如 01 素材" onChange={e => set({ eyebrow: e.target.value })} /></Field>
        <Field label="标亮的词"><input className="input" value={scene.accent || ''} onChange={e => set({ accent: e.target.value })} /></Field>
      </div>
      <Field label="标题"><input className="input" value={scene.title || ''} onChange={e => set({ title: e.target.value })} /></Field>
    </>
  )
  switch (scene.type) {
    case 'hook':
      return (
        <>
          <Field label="大字" hint="一行一句，每行 6 个字以内"><AutoTextarea className="textarea" value={(scene.words || []).join('\n')} onChange={e => set({ words: toLines(e.target.value).slice(0, 2) })} /></Field>
          <div className="vgrid2">
            <Field label="标亮的词"><input className="input" value={scene.accent || ''} onChange={e => set({ accent: e.target.value })} /></Field>
            <Field label="框上的话" hint="可选"><input className="input" value={scene.box || ''} placeholder="如 全交给 AI" onChange={e => set({ box: e.target.value })} /></Field>
          </div>
          <Field label="大字前的引子" hint="可选"><input className="input" value={scene.lead || ''} onChange={e => set({ lead: e.target.value })} /></Field>
          <Field label="小卡片" hint="一行一张，最多 4 张；“文字｜小字”"><AutoTextarea className="textarea" value={itemsText(scene.chips, true)} onChange={e => set({ chips: textItems(e.target.value, scene.chips, true).slice(0, 4) })} /></Field>
        </>
      )
    case 'points':
      return (
        <>
          {head}
          <Field label="卡片" hint="一行一张，最多 4 张；“文字｜小字”"><AutoTextarea className="textarea" value={itemsText(scene.items, true)} onChange={e => set({ items: textItems(e.target.value, scene.items, true).slice(0, 4) })} /></Field>
          <Field label="卡片下面的大字" hint="可选"><input className="input" value={scene.footer || ''} onChange={e => set({ footer: e.target.value })} /></Field>
        </>
      )
    case 'shot':
      return (
        <>
          {head}
          <Field label="截图"><ImagePick images={images} value={scene.image} onPick={id => set({ image: id, focus: undefined })} /></Field>
          {img && <FocusPicker image={img} focus={scene.focus} onChange={f => set({ focus: f })} />}
          <div className="vgrid2">
            <Field label="框下面的标注" hint="可选"><input className="input" value={scene.label || ''} onChange={e => set({ label: e.target.value })} /></Field>
            <Field label="角上的徽章" hint="可选，逗号隔开"><input className="input" value={(scene.badges || []).map(b => b.text).join('，')}
              onChange={e => set({ badges: textItems(e.target.value.replace(/[,，]/g, '\n'), scene.badges, false).slice(0, 2) })} /></Field>
          </div>
          <Field label="读到一半换成的标题" hint="可选"><input className="input" value={scene.title2 || ''} onChange={e => set({ title2: e.target.value })} /></Field>
        </>
      )
    case 'stat':
      return (
        <>
          {head}
          <div className="vgrid2">
            <Field label="数字"><input className="input" inputMode="numeric" value={scene.value || ''} onChange={e => set({ value: e.target.value })} /></Field>
            <Field label="数字下面的字"><input className="input" value={scene.unit || ''} onChange={e => set({ unit: e.target.value })} /></Field>
            <Field label="再变成" hint="可选"><input className="input" inputMode="numeric" value={scene.then || ''} onChange={e => set({ then: e.target.value })} /></Field>
            <Field label="变成后下面的字"><input className="input" value={scene.then_unit || ''} onChange={e => set({ then_unit: e.target.value })} /></Field>
          </div>
          <Field label="一行小字" hint="可选"><input className="input" value={scene.sub || ''} onChange={e => set({ sub: e.target.value })} /></Field>
        </>
      )
    case 'change':
      return (
        <>
          {head}
          <Field label="对比" hint="一行一组：标签｜原来｜改成"><AutoTextarea className="textarea" value={rowsText(scene.rows)} onChange={e => set({ rows: textRows(e.target.value, scene.rows).slice(0, 3) })} /></Field>
          <div className="vgrid2">
            <Field label="下面的大字" hint="可选"><input className="input" value={scene.footer || ''} onChange={e => set({ footer: e.target.value })} /></Field>
            <Field label="大字里标亮的词"><input className="input" value={scene.footer_accent || ''} onChange={e => set({ footer_accent: e.target.value })} /></Field>
          </div>
        </>
      )
    case 'quote':
      return (
        <>
          <Field label="前面一句小字" hint="可选"><input className="input" value={scene.lead || ''} onChange={e => set({ lead: e.target.value })} /></Field>
          <Field label="金句" hint="一行一句，每行 12 个字以内"><AutoTextarea className="textarea" value={(scene.lines || []).join('\n')} onChange={e => set({ lines: toLines(e.target.value).slice(0, 3) })} /></Field>
          <Field label="标亮的词"><input className="input" value={scene.accent || ''} onChange={e => set({ accent: e.target.value })} /></Field>
        </>
      )
    case 'cta':
      return (
        <>
          <Field label="截图" hint="可选，比如仓库首页"><ImagePick images={images} value={scene.image} onPick={id => set({ image: id })} /></Field>
          <div className="vgrid2">
            <Field label="徽章"><input className="input" value={scene.badge || ''} placeholder="如 已开源" onChange={e => set({ badge: e.target.value })} /></Field>
            <Field label="小标签" hint="逗号隔开"><input className="input" value={(scene.chips || []).map(b => b.text).join('，')}
              onChange={e => set({ chips: textItems(e.target.value.replace(/[,，]/g, '\n'), scene.chips, false).slice(0, 3) })} /></Field>
          </div>
          <Field label="命令或链接" hint="会在终端框里打出来"><input className="input" value={scene.command || ''} onChange={e => set({ command: e.target.value })} /></Field>
          <div className="vgrid2">
            <Field label="署名"><input className="input" value={scene.sign || ''} onChange={e => set({ sign: e.target.value })} /></Field>
            <Field label="署名下面的小字"><input className="input" value={scene.sign_sub || ''} onChange={e => set({ sign_sub: e.target.value })} /></Field>
          </div>
        </>
      )
  }
}

/** The video of one long-form draft: script, voice, render job and the actions on
 * them. Shared by the editor's 视频 card and the full-screen video workspace. */
export function useVideo(draft: Draft | null, onDraft: (d: Draft) => void) {
  const toast = useToast()
  const [s, setS] = useState<VideoState>(draft?.video || {})
  const [opts, setOpts] = useState<VideoOptions | null>(null)
  const [busy, setBusy] = useState('')
  const timer = useRef<number | undefined>(undefined)
  const pending = useRef<VideoScene[] | null>(null)
  const was = useRef(s.status)
  const id = draft?.id

  useEffect(() => { api.get<VideoOptions>('/api/video/options').then(setOpts).catch(() => {}) }, [])
  useEffect(() => { setS(draft?.video || {}); was.current = draft?.video?.status },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, draft?.video?.planned_at, draft?.video?.video?.rendered_at])

  // follow the job while the AI writes or the machine renders
  useEffect(() => {
    if (!s.status || !id) return
    const t = window.setInterval(async () => {
      const d = await api.get<Draft>(`/api/drafts/${id}`).catch(() => null)
      if (!d) return
      const v = d.video || {}
      setS(prev => ({ ...prev, status: v.status, stage: v.stage, pct: v.pct, error: v.error, video: v.video,
        ...(prev.status === 'planning' ? { scenes: v.scenes, planned_at: v.planned_at, edited_at: v.edited_at } : {}) }))
      if (!v.status) {
        onDraft(d)
        if (v.error) toast(v.error, 'error')
        else if (was.current === 'rendering') toast('视频出好了')
        else if (was.current === 'planning') toast('脚本写好了，改完点“出片”')
      }
      was.current = v.status
    }, 3000)
    return () => window.clearInterval(t)
  }, [s.status, id, onDraft, toast])

  const flush = useCallback(async () => {
    window.clearTimeout(timer.current)
    const next = pending.current
    pending.current = null
    if (next && id) await api.put(`/api/drafts/${id}/video`, { scenes: next }).catch(e => toast((e as Error).message, 'error'))
  }, [id, toast])
  // leaving the page saves what was typed in the last moment
  useEffect(() => () => { void flush() }, [flush])

  const scenes = s.scenes || []
  function saveScenes(next: VideoScene[]) {
    setS(st => ({ ...st, scenes: next, edited_at: Date.now() / 1000 }))
    pending.current = next
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(flush, 700)
  }
  async function plan() {
    if (!id) return
    if (scenes.length && !window.confirm('按现在的长文重新写视频脚本？你改过的镜头会被覆盖。')) return
    try { const next = await api.post<VideoState>(`/api/drafts/${id}/video/plan`); was.current = next.status; setS(next) } catch (e) { toast((e as Error).message, 'error') }
  }
  async function render() {
    if (!id) return
    setBusy('render')
    try {
      await flush()
      const next = await api.post<VideoState>(`/api/drafts/${id}/video/render`)
      was.current = next.status
      setS(st => ({ ...st, status: next.status, stage: next.stage, pct: next.pct, error: null }))
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }
  async function setVoice(voice: string) {
    setS(st => ({ ...st, voice }))
    await api.put(`/api/drafts/${id}/video`, { voice }).catch(e => toast((e as Error).message, 'error'))
  }
  async function setBgm(bgm: boolean) {
    setS(st => ({ ...st, bgm }))
    await api.put(`/api/drafts/${id}/video`, { bgm }).catch(e => toast((e as Error).message, 'error'))
  }
  const src = s.video && id ? `/api/drafts/${id}/video.mp4?v=${Math.round(s.video.rendered_at)}` : ''
  async function share() {
    setBusy('share')
    try {
      const blob = await (await fetch(src)).blob()
      const file = new File([blob], `${(draft?.title || '视频').slice(0, 20)}.mp4`, { type: 'video/mp4' })
      if (!navigator.canShare || !navigator.canShare({ files: [file] })) { toast('这台设备不支持直接分享视频，用“下载 MP4”', 'error'); return }
      await navigator.share({ files: [file] })
    } catch (e) {
      if ((e as Error).name !== 'AbortError') toast((e as Error).message, 'error')
    } finally { setBusy('') }
  }
  return {
    s, opts, busy, scenes, src, saveScenes, flush, plan, render, setVoice, setBgm, share,
    total: scenes.reduce((n, sc) => n + speakSec(sc.say || ''), 0),
    stale: !!(s.video && s.edited_at && s.edited_at > s.video.rendered_at),
  }
}

export function TtsWarning({ opts }: { opts: VideoOptions | null }) {
  if (!opts || opts.tts) return null
  return <div className="vnote warn small"><Ic.Alert size={14} /> 还没配置配音用的 Evolink key（设置文件里的 evolink_key_file），能写脚本，但出不了片。</div>
}

export function RenderProgress({ s }: { s: VideoState }) {
  return (
    <div className="vprogress">
      <div className="row small" style={{ justifyContent: 'space-between' }}><span><Ic.Loader size={13} /> {s.stage || '出片中'}</span><span className="faint">{s.pct || 0}%</span></div>
      <div className="vbar"><i style={{ width: `${Math.max(3, s.pct || 0)}%` }} /></div>
      <div className="small faint">在这台电脑上配音、对齐字幕、渲染，可以先去干别的，好了会在这里出现。</div>
    </div>
  )
}

/** The 视频 tab beside the long-form editor: where the video stands, and the way
 * into the full-screen video workspace where it is made. */
export function VideoCard({ draft, onDraft }: { draft: Draft; onDraft: (d: Draft) => void }) {
  const v = useVideo(draft, onDraft)
  const { s, scenes } = v
  const open = async () => { await v.flush(); go(`videos/${draft.id}`) }
  return (
    <div className="stack video">
      {s.status === 'planning' ? (
        <div className="row muted small"><Ic.Loader size={14} /> 正在看文章里的截图、写分镜脚本，一般两三分钟。</div>
      ) : s.status === 'rendering' ? <RenderProgress s={s} /> : s.video ? (
        <div className="vresult">
          <video key={v.src} className="vplayer" src={v.src} controls playsInline preload="metadata" />
          <div className="vresult-side">
            <div className="small muted">{fmtDur(s.video.duration)} · 1080×1920 · {fmtSize(s.video.size)}</div>
            {v.stale && <div className="vnote small"><Ic.Alert size={13} /> 脚本改过了，重新出片才会更新。</div>}
            <a className="btn sm" href={`/api/drafts/${draft.id}/video.mp4?download=1`}><Ic.Download size={14} /> 下载 MP4</a>
            <button className="btn sm mobile-share" disabled={!!v.busy} onClick={v.share}>{v.busy === 'share' ? <Ic.Loader size={14} /> : <Ic.Send size={14} />} 分享到手机 App</button>
          </div>
        </div>
      ) : (
        <div className="small muted">把这篇长文做成一条竖屏口播视频，发抖音、视频号和小红书。AI 先写分镜脚本，你在视频工作区里一个镜头一个镜头地改、当场预览，满意了再出片。</div>
      )}
      {s.error && !s.status && <div className="err small">{s.error}</div>}
      {scenes.length > 0 && <div className="small"><b>{scenes.length}</b> 个镜头 · 约 {fmtDur(v.total)}</div>}
      <button className="btn primary" onClick={open}><Ic.Film size={15} /> 打开视频工作区</button>
    </div>
  )
}
