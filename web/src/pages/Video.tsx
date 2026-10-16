import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type Draft, type DraftImage, type VideoItem, type VideoOptions, type VideoRow, type VideoScene, type VideoState } from '../api'
import { AutoTextarea, useToast } from '../ui'
import { imgUrl } from './Longform'
import * as Ic from '../icons'

const TYPES: VideoScene['type'][] = ['hook', 'points', 'shot', 'stat', 'change', 'quote', 'cta']
const TYPE_LABEL: Record<VideoScene['type'], string> = {
  hook: '开头大字', points: '要点卡片', shot: '截图放大', stat: '数字', change: '前后对比', quote: '金句', cta: '结尾',
}
const TYPE_HINT: Record<VideoScene['type'], string> = {
  hook: '一两行大字抛出这条视频的看点，可以再跟几张小卡片',
  points: '标题加 1 到 4 张卡片，读到哪条出哪条',
  shot: '真实截图放进窗口，读到某句时推近到框出的区域',
  stat: '一个数字滚动出来，可以再变成另一个数字',
  change: '原来的说法被划掉，改后的说法打出来',
  quote: '一句大字观点，标亮一个词',
  cta: '开源地址或链接、署名，片子在这里收尾',
}

const speakSec = (say: string) => say.replace(/[\s，。？！、：；,.?!:;“”]/g, '').length / 5.1 + 0.5
const fmtDur = (sec: number) => (sec >= 60 ? `${Math.floor(sec / 60)} 分 ${Math.round(sec % 60)} 秒` : `${Math.round(sec)} 秒`)
const toLines = (text: string) => text.split('\n').map(x => x.trim()).filter(Boolean)
const fmtSize = (b: number) => (b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.round(b / 1e3)} KB`)

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

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
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

function SceneEditor({ scene, images, set }: { scene: VideoScene; images: DraftImage[]; set: (patch: Partial<VideoScene>) => void }) {
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

/** Short vertical video of the long-form draft: AI script → author edits → rendered here. */
export function VideoPanel({ draft, onDraft }: { draft: Draft; onDraft: (d: Draft) => void }) {
  const toast = useToast()
  const [s, setS] = useState<VideoState>(draft.video || {})
  const [opts, setOpts] = useState<VideoOptions | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [busy, setBusy] = useState('')
  const timer = useRef<number | undefined>(undefined)
  const pending = useRef<VideoScene[] | null>(null)
  const was = useRef(s.status)

  useEffect(() => { api.get<VideoOptions>('/api/video/options').then(setOpts).catch(() => {}) }, [])
  useEffect(() => { setS(draft.video || {}) }, [draft.id, draft.video?.planned_at, draft.video?.video?.rendered_at])

  // follow the job while the AI writes or the machine renders
  useEffect(() => {
    if (!s.status) return
    const t = window.setInterval(async () => {
      const d = await api.get<Draft>(`/api/drafts/${draft.id}`).catch(() => null)
      if (!d) return
      const v = d.video || {}
      setS(prev => ({ ...prev, status: v.status, stage: v.stage, pct: v.pct, error: v.error, video: v.video,
        ...(prev.status === 'planning' ? { scenes: v.scenes, planned_at: v.planned_at, edited_at: v.edited_at } : {}) }))
      if (!v.status) {
        onDraft(d)
        if (v.error) toast(v.error, 'error')
        else if (was.current === 'rendering') toast('视频出好了')
        else if (was.current === 'planning') { toast('脚本写好了，改完点“出片”'); setOpen(0) }
      }
      was.current = v.status
    }, 3000)
    return () => window.clearInterval(t)
  }, [s.status, draft.id, onDraft, toast])

  const scenes = s.scenes || []
  const images = (draft.images || []).filter(im => !im.name.startsWith('小红书-'))
  const total = scenes.reduce((n, sc) => n + speakSec(sc.say || ''), 0)
  const stale = !!(s.video && s.edited_at && s.edited_at > s.video.rendered_at)

  function saveScenes(next: VideoScene[]) {
    setS(st => ({ ...st, scenes: next, edited_at: Date.now() / 1000 }))
    pending.current = next
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(flush, 700)
  }
  async function flush() {
    window.clearTimeout(timer.current)
    const next = pending.current
    pending.current = null
    if (next) await api.put(`/api/drafts/${draft.id}/video`, { scenes: next }).catch(e => toast((e as Error).message, 'error'))
  }
  const setScene = (i: number, patch: Partial<VideoScene>) => saveScenes(scenes.map((sc, j) => (j === i ? { ...sc, ...patch } : sc)))
  function move(i: number, d: -1 | 1) {
    const j = i + d
    if (j < 0 || j >= scenes.length) return
    const next = [...scenes]
    ;[next[i], next[j]] = [next[j], next[i]]
    saveScenes(next)
    setOpen(j)
  }
  function remove(i: number) {
    if (!window.confirm(`删掉第 ${i + 1} 个镜头？`)) return
    saveScenes(scenes.filter((_, j) => j !== i))
    setOpen(null)
  }
  function add() {
    saveScenes([...scenes, { type: 'points', say: '', title: '' }])
    setOpen(scenes.length)
  }

  async function plan() {
    if (scenes.length && !window.confirm('按现在的长文重新写视频脚本？你改过的镜头会被覆盖。')) return
    try { const next = await api.post<VideoState>(`/api/drafts/${draft.id}/video/plan`); was.current = next.status; setS(next) } catch (e) { toast((e as Error).message, 'error') }
  }
  async function render() {
    setBusy('render')
    try {
      await flush()
      const next = await api.post<VideoState>(`/api/drafts/${draft.id}/video/render`)
      was.current = next.status
      setS(st => ({ ...st, status: next.status, stage: next.stage, pct: next.pct, error: null }))
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }
  async function setVoice(voice: string) {
    setS(st => ({ ...st, voice }))
    await api.put(`/api/drafts/${draft.id}/video`, { voice }).catch(e => toast((e as Error).message, 'error'))
  }
  async function setBgm(bgm: boolean) {
    setS(st => ({ ...st, bgm }))
    await api.put(`/api/drafts/${draft.id}/video`, { bgm }).catch(e => toast((e as Error).message, 'error'))
  }
  const src = s.video ? `/api/drafts/${draft.id}/video.mp4?v=${Math.round(s.video.rendered_at)}` : ''
  async function share() {
    setBusy('share')
    try {
      const blob = await (await fetch(src)).blob()
      const file = new File([blob], `${(draft.title || '视频').slice(0, 20)}.mp4`, { type: 'video/mp4' })
      if (!navigator.canShare || !navigator.canShare({ files: [file] })) { toast('这台设备不支持直接分享视频，用“下载 MP4”', 'error'); return }
      await navigator.share({ files: [file] })
    } catch (e) {
      if ((e as Error).name !== 'AbortError') toast((e as Error).message, 'error')
    } finally { setBusy('') }
  }

  const warn = opts && !opts.tts
    ? <div className="vnote warn small"><Ic.Alert size={14} /> 还没配置配音用的 Evolink key（设置文件里的 evolink_key_file），能写脚本，但出不了片。</div>
    : null

  if (s.status === 'planning') return (
    <div className="stack">
      <div className="row muted small"><Ic.Loader size={14} /> 正在看文章里的截图、写分镜脚本，一般两三分钟。</div>
      <div className="skeleton" style={{ height: 120 }} /><div className="skeleton" style={{ height: 120 }} />
    </div>
  )

  if (!scenes.length) return (
    <div className="stack">
      <div className="small muted">把这篇长文做成一条竖屏口播视频，发抖音、视频号和小红书。AI 先写分镜脚本：每个镜头说什么、画面上出什么字、用哪张截图、推近到哪一块。你改完点“出片”，在这台电脑上配音、对齐字幕、渲染成 MP4，两分钟的片子大约要三到五分钟。</div>
      {warn}
      {s.error && <div className="err small">{s.error}</div>}
      <button className="btn primary" onClick={plan}><Ic.Sparkle size={14} /> 生成视频脚本</button>
      <button className="btn ghost sm" onClick={add}><Ic.Plus size={13} /> 不用 AI，自己从空白写</button>
    </div>
  )

  return (
    <div className="stack video">
      {warn}
      {s.status === 'rendering' ? (
        <div className="vprogress">
          <div className="row small" style={{ justifyContent: 'space-between' }}><span><Ic.Loader size={13} /> {s.stage || '出片中'}</span><span className="faint">{s.pct || 0}%</span></div>
          <div className="vbar"><i style={{ width: `${Math.max(3, s.pct || 0)}%` }} /></div>
          <div className="small faint">在这台电脑上配音、对齐字幕、渲染，可以先去干别的，好了会在这里出现。</div>
        </div>
      ) : s.video ? (
        <div className="vresult">
          <video key={src} className="vplayer" src={src} controls playsInline preload="metadata" />
          <div className="vresult-side">
            <div className="small muted">{fmtDur(s.video.duration)} · 1080×1920 · {fmtSize(s.video.size)}</div>
            {stale && <div className="vnote small"><Ic.Alert size={13} /> 脚本改过了，重新出片才会更新。</div>}
            <button className="btn primary sm" disabled={!!busy} onClick={render}><Ic.Film size={14} /> 重新出片</button>
            <a className="btn sm" href={`/api/drafts/${draft.id}/video.mp4?download=1`}><Ic.Download size={14} /> 下载 MP4</a>
            <button className="btn sm mobile-share" disabled={!!busy} onClick={share}>{busy === 'share' ? <Ic.Loader size={14} /> : <Ic.Send size={14} />} 分享到手机 App</button>
          </div>
        </div>
      ) : null}
      {s.error && !s.status && <div className="err small">{s.error}</div>}

      <div className="vbar-head">
        <div className="small"><b>{scenes.length}</b> 个镜头 · 约 {fmtDur(total)}</div>
        <div className="row" style={{ gap: 6 }}>
          {opts && (
            <select className="input sm" value={s.voice || opts.default_voice} onChange={e => setVoice(e.target.value)} aria-label="配音音色">
              {opts.voices.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
            </select>
          )}
          {opts?.bgm && (
            <label className="vcheck small"><input type="checkbox" checked={s.bgm !== false} onChange={e => setBgm(e.target.checked)} /> 配乐</label>
          )}
        </div>
      </div>
      {!s.video && s.status !== 'rendering' && (
        <button className="btn primary" disabled={!!busy || !scenes.some(sc => sc.say.trim())} onClick={render}><Ic.Film size={15} /> 出片</button>
      )}

      <ol className="vscenes">
        {scenes.map((sc, i) => {
          const isOpen = open === i
          const clip = s.video?.scenes?.[i]
          return (
            <li key={i} className={'vscene' + (isOpen ? ' open' : '')}>
              <button type="button" className="vscene-head" onClick={() => setOpen(isOpen ? null : i)} aria-expanded={isOpen}>
                <span className="vnum">{i + 1}</span>
                <span className="vtype">{TYPE_LABEL[sc.type]}</span>
                <span className="vsay">{sc.say || <i className="faint">还没写口播</i>}</span>
                <span className="faint small vsec">{Math.round(speakSec(sc.say || ''))}″</span>
              </button>
              {isOpen && (
                <div className="vscene-body">
                  <div className="row" style={{ gap: 6, justifyContent: 'space-between' }}>
                    <select className="input sm" value={sc.type} onChange={e => setScene(i, { type: e.target.value as VideoScene['type'] })} aria-label="镜头类型">
                      {TYPES.map(t => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
                    </select>
                    <div className="row" style={{ gap: 2 }}>
                      {clip && <button type="button" className="btn ghost icon sm" title="听上次出片的这段配音"
                        onClick={() => new Audio(`/api/drafts/${draft.id}/video/voice/${i + 1}?v=${Math.round(s.video!.rendered_at)}`).play()}><Ic.Play size={13} /></button>}
                      <button type="button" className="btn ghost icon sm" aria-label="往前" disabled={i === 0} onClick={() => move(i, -1)}><Ic.Up size={14} /></button>
                      <button type="button" className="btn ghost icon sm" aria-label="往后" disabled={i === scenes.length - 1} onClick={() => move(i, 1)}><Ic.Down size={14} /></button>
                      <button type="button" className="btn ghost icon sm" aria-label="删掉这个镜头" onClick={() => remove(i)}><Ic.Trash size={14} /></button>
                    </div>
                  </div>
                  <div className="small faint">{TYPE_HINT[sc.type]}</div>
                  <Field label="口播" hint="这个镜头读出来的话">
                    <AutoTextarea className="textarea vsay-input" value={sc.say} onChange={e => setScene(i, { say: e.target.value })} />
                  </Field>
                  <SceneEditor scene={sc} images={images} set={p => setScene(i, p)} />
                </div>
              )}
            </li>
          )
        })}
      </ol>
      <div className="row" style={{ gap: 6 }}>
        <button className="btn ghost sm" onClick={add}><Ic.Plus size={13} /> 加一个镜头</button>
        <button className="btn ghost sm" onClick={plan}><Ic.Sparkle size={13} /> 重新生成脚本</button>
      </div>
      <div className="small faint">口播里的数字和英文名照常写，配音会自动读对；读错的字可以换个说法。画面上的字从口播里提炼，读到哪里出到哪里。</div>
    </div>
  )
}
