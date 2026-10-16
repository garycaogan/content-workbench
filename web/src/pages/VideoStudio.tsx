import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Draft, type VideoPreview, type VideoScene } from '../api'
import { go } from '../App'
import { AutoTextarea, Empty } from '../ui'
import { Field, fmtDur, fmtSize, RenderProgress, SceneEditor, speakSec, TtsWarning, TYPE_HINT, TYPE_LABEL, TYPES, useVideo } from './Video'
import * as Ic from '../icons'

type Frame = Window & { cwSeek?: (t: number) => void; cwPoster?: number }
const fmtSec = (t: number) => t.toFixed(1)

/** One scene played in the browser, without rendering: the server builds the
 * scene's page (same composition, captions and author mark as the render) and
 * this drives its timeline from the voice. Edits rebuild it a moment after you
 * stop typing; a changed line plays silent on estimated timing until you ask
 * for the voice, so typing never calls the TTS. */
function ScenePreview({ draftId, index, scene, last, tts }: { draftId: number; index: number; scene: VideoScene; last: boolean; tts: boolean }) {
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
      const r = await api.post<VideoPreview>(`/api/drafts/${draftId}/video/preview`, { index, scene, last, speak })
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
  const key = JSON.stringify([index, last, scene])
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
      <div className="spv-frame">
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

/** Full-screen workspace for the video of one long-form draft: the scenes on the
 * left, the chosen scene in the middle, and on the right an instant preview of
 * that scene or the rendered video, which jumps to whichever scene you pick. */
export function VideoStudio({ id }: { id: number }) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const [missing, setMissing] = useState(false)
  const [sel, setSel] = useState(0)
  const [mode, setMode] = useState<'scene' | 'video'>('scene')
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
  useEffect(() => { if (!s.video) setMode('scene') }, [s.video])

  // the render leaves out scenes with nothing to say, so map list positions onto rendered ones
  const spoken = scenes.map((sc, i) => (sc.say.trim() ? scenes.slice(0, i).filter(x => x.say.trim()).length : -1))
  const startOf = (i: number) => (spoken[i] >= 0 ? s.video?.scenes?.[spoken[i]]?.start : undefined)
  function seekVideo(i: number, play: boolean) {
    const at = startOf(i)
    const el = player.current
    if (at === undefined || !el) return
    el.currentTime = at + 0.05
    if (play) el.play().catch(() => {})
  }
  function onVideoTime() {
    const el = player.current
    if (!el || !s.video) return
    const k = s.video.scenes.findIndex(x => el.currentTime >= x.start && el.currentTime < x.start + x.duration)
    setPlayingScene(k < 0 ? null : spoken.indexOf(k))
  }
  function pick(i: number) {
    setSel(i)
    if (mode === 'video') seekVideo(i, true)
  }
  function showVideo() {
    setMode('video')
    window.setTimeout(() => seekVideo(sel, false), 50)
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
  const canRender = scenes.some(sc => sc.say.trim())

  return (
    <div className="studio">
      <header className="studio-top">
        <button className="btn ghost icon" aria-label="回到视频列表" onClick={() => back('videos')}><Ic.Back /></button>
        <div className="crumb">
          <span className="pill"><Ic.Film size={12} /> 视频</span>
          <span className="t">{draft.title || '（还没有标题）'}</span>
        </div>
        <button className="btn ghost sm studio-doc" onClick={() => back(`drafts/${id}`)}><Ic.Doc size={14} /> 长文</button>
        {scenes.length > 0 && (
          <div className="right">
            {opts && (
              <select className="input sm" value={s.voice || opts.default_voice} onChange={e => v.setVoice(e.target.value)} aria-label="配音音色">
                {opts.voices.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}
              </select>
            )}
            {opts?.bgm && <label className="vcheck small"><input type="checkbox" checked={s.bgm !== false} onChange={e => v.setBgm(e.target.checked)} /> 配乐</label>}
            {s.status === 'rendering'
              ? <span className="pill accent"><Ic.Loader size={12} /> 出片中 {s.pct || 0}%</span>
              : <button className="btn primary sm" disabled={!!v.busy || !canRender || s.status === 'planning'} onClick={v.render}>
                <Ic.Film size={14} /> {s.video ? '重新出片' : '出片'}
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
              <button className="btn primary" disabled={!draft.body.trim()} onClick={v.plan}><Ic.Sparkle size={14} /> 生成视频脚本</button>
              <button className="btn ghost sm" onClick={add}><Ic.Plus size={13} /> 不用 AI，自己从空白写</button>
            </div>
          </div>
        </div>
      ) : (
        <div className="studio-body">
          <aside className="studio-scenes" aria-label="镜头">
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

          <section className="studio-edit" aria-label="编辑镜头">
            {scene && (
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
                <SceneEditor scene={scene} images={images} set={patch => setScene(sel, patch)} />
                <div className="small faint">口播里的数字和英文名照常写，配音会自动读对；读错的字可以换个说法。画面上的字从口播里提炼，读到哪里出到哪里。</div>
              </div>
            )}
          </section>

          <aside className="studio-preview" aria-label="预览">
            <div className="segmented" role="group" aria-label="预览什么">
              <button aria-pressed={mode === 'scene'} onClick={() => setMode('scene')}>这个镜头</button>
              <button aria-pressed={mode === 'video'} disabled={!s.video} onClick={showVideo} title={s.video ? '' : '还没出片'}>成片</button>
            </div>
            {mode === 'video' && s.video ? (
              <div className="spv">
                <div className="spv-frame">
                  <video ref={player} key={v.src} src={v.src} controls playsInline preload="metadata" onTimeUpdate={onVideoTime} />
                </div>
                <div className="small muted">{fmtDur(s.video.duration)} · 1080×1920 · {fmtSize(s.video.size)} · 点左边的镜头跳到那一段</div>
                {v.stale && <div className="vnote small"><Ic.Alert size={13} /> 脚本改过了，这是改之前的成片，重新出片才会更新。</div>}
                <div className="row">
                  <a className="btn sm" href={`/api/drafts/${id}/video.mp4?download=1`}><Ic.Download size={14} /> 下载 MP4</a>
                  <button className="btn sm mobile-share" disabled={!!v.busy} onClick={v.share}>{v.busy === 'share' ? <Ic.Loader size={14} /> : <Ic.Send size={14} />} 分享到手机 App</button>
                </div>
              </div>
            ) : scene ? (
              <ScenePreview draftId={id} index={sel} scene={scene} last={sel === scenes.length - 1} tts={!!opts?.tts} />
            ) : null}
            {s.status === 'rendering' && <RenderProgress s={s} />}
            {s.error && !s.status && <div className="err small">{s.error}</div>}
            <TtsWarning opts={opts} />
          </aside>
        </div>
      )}
    </div>
  )
}
