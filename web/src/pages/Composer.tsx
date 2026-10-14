import { useEffect, useRef, useState } from 'react'
import { api, type Status } from '../api'
import { AutoTextarea, Dialog, useToast } from '../ui'
import * as Ic from '../icons'
import { FeishuLibrary } from './FeishuLibrary'

const NOTE_KEY = 'cw-note-draft'
const FEISHU_LINK = /^\s*(https?:\/\/[\w.-]*(feishu\.cn|larksuite\.com|larkoffice\.com)\/(docx|docs|wiki|doc)\/[\w-]+[^\s]*)\s*$/i

function loadNote(): string {
  try { return localStorage.getItem(NOTE_KEY) || '' } catch { return '' }
}
function saveNote(v: string) {
  try { localStorage.setItem(NOTE_KEY, v) } catch { /* private mode */ }
}

/** One box for everything the author wants to capture: typed notes, a pasted Feishu
 *  link, a voice recording, an audio file, or a Feishu Minutes recording. */
export function Composer({ status, onSaved }: { status: Status | null; onSaved: () => void }) {
  const toast = useToast()
  const [text, setText] = useState(loadNote())
  const [busy, setBusy] = useState(false)
  const [minutesOpen, setMinutesOpen] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const box = useRef<HTMLTextAreaElement | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const rec = useRecorder(file => upload(file))
  const link = text.match(FEISHU_LINK)?.[1]

  useEffect(() => {
    const focus = () => { box.current?.focus(); box.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }) }
    window.addEventListener('cw-capture', focus)
    return () => window.removeEventListener('cw-capture', focus)
  }, [])

  function change(v: string) { setText(v); saveNote(v) }

  async function submit() {
    if (!text.trim() || busy) return
    setBusy(true)
    try {
      if (link) {
        await api.post('/api/materials/feishu-doc', { url: link, extract: true })
        toast('飞书文档已导入，正在提炼选题')
      } else {
        await api.post('/api/materials', { body: text, extract: true })
        toast('记下了，正在提炼选题')
      }
      change('')
      onSaved()
      window.dispatchEvent(new Event('cw-refresh-status'))
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }

  async function upload(file: File) {
    const form = new FormData()
    form.append('file', file)
    form.append('title', file.name.replace(/\.\w+$/, ''))
    form.append('extract', '1')
    try {
      await api.upload('/api/materials/audio', form)
      toast('录音已上传，转写完会自动提炼')
      onSaved()
      window.dispatchEvent(new Event('cw-refresh-status'))
    } catch (e) {
      toast('上传失败，录音已下载到本地：' + (e as Error).message, 'error')
      const url = URL.createObjectURL(file)
      const a = document.createElement('a'); a.href = url; a.download = file.name; a.click()
    }
  }

  if (rec.active) {
    return (
      <div className="composer">
        <div className="recording">
          <span className="rec-dot" />
          <span className="rec-time">{fmt(rec.secs)}</span>
          <span className="bars" aria-hidden="true">{Array.from({ length: 14 }, (_, i) => <i key={i} />)}</span>
          <span className="muted small grow">正在录音，说完点结束，会在本机转写</span>
          <button className="btn ghost" onClick={rec.cancel}>放弃</button>
          <button className="btn primary" onClick={rec.stop}><Ic.Stop size={15} /> 结束并保存</button>
        </div>
      </div>
    )
  }

  return (
    <div className="composer">
      <AutoTextarea inputRef={box} value={text} placeholder="记点什么。今天想到的、看到的、跟人聊到的，或者直接贴一个飞书文档链接。"
        onChange={e => change(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit() } }} />
      <div className="composer-bar">
        <button className="btn ghost sm" onClick={rec.start} title="录音"><Ic.Mic size={16} /> 录音</button>
        <button className="btn ghost sm" onClick={() => fileRef.current?.click()} title="上传音频"><Ic.Upload size={16} /> 音频文件</button>
        <button className="btn ghost sm" onClick={() => setMinutesOpen(true)} title="飞书妙记"><Ic.Wave size={16} /> 飞书妙记</button>
        <button className="btn ghost sm" onClick={() => setLibraryOpen(true)} title="从飞书文档导入"><Ic.Feishu size={16} /> 飞书文档</button>
        <input ref={fileRef} type="file" accept="audio/*,video/mp4,video/quicktime" hidden
          onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
        {status && !status.transcription && <span className="hint">本机转写还没装好</span>}
        <div className="push">
          {link && <span className="link-chip"><Ic.Feishu size={13} /> 会导入这篇飞书文档的全文</span>}
          {!link && text.trim() && <span className="hint">⌘ + Enter 保存</span>}
          <button className="btn primary" disabled={!text.trim() || busy} onClick={submit}>
            {busy ? <Ic.Loader size={15} /> : <Ic.Sparkle size={15} />} {link ? '导入并提炼' : '保存并提炼'}
          </button>
        </div>
      </div>
      {rec.error && <div className="err" style={{ padding: '0 16px 12px' }}>{rec.error}</div>}
      <MinutesDialog open={minutesOpen} onClose={() => setMinutesOpen(false)} onImported={onSaved} />
      <FeishuLibrary open={libraryOpen} onClose={() => setLibraryOpen(false)} onImported={onSaved} />
    </div>
  )
}

function fmt(s: number) { return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` }

function useRecorder(onFile: (f: File) => void) {
  const [active, setActive] = useState(false)
  const [secs, setSecs] = useState(0)
  const [error, setError] = useState('')
  const mr = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const discard = useRef(false)
  const timer = useRef<number | undefined>(undefined)

  async function start() {
    setError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const r = new MediaRecorder(stream)
      chunks.current = []; discard.current = false
      r.ondataavailable = e => { if (e.data.size) chunks.current.push(e.data) }
      r.onstop = () => {
        stream.getTracks().forEach(t => t.stop())
        if (discard.current) return
        const type = r.mimeType || 'audio/webm'
        const ext = type.includes('mp4') ? 'mp4' : 'webm'
        const stamp = new Date().toLocaleString('zh-CN', { hour12: false }).replace(/[/:\s]/g, '')
        onFile(new File(chunks.current, `录音-${stamp}.${ext}`, { type }))
      }
      r.start(1000)
      mr.current = r
      setActive(true); setSecs(0)
      timer.current = window.setInterval(() => setSecs(s => s + 1), 1000)
    } catch (e) { setError('打不开麦克风：' + (e as Error).message) }
  }
  function end(keep: boolean) {
    discard.current = !keep
    mr.current?.stop(); mr.current = null
    window.clearInterval(timer.current)
    setActive(false)
  }
  return { active, secs, error, start, stop: () => end(true), cancel: () => end(false) }
}

type Minute = { token: string; title: string; description: string; imported: boolean }

function MinutesDialog({ open, onClose, onImported }: { open: boolean; onClose: () => void; onImported: () => void }) {
  const toast = useToast()
  const [days, setDays] = useState(7)
  const [list, setList] = useState<Minute[] | null>(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (!open) return
    let alive = true
    setList(null); setErr('')
    const end = new Date(); const start = new Date(Date.now() - days * 86400000)
    const f = (d: Date) => d.toISOString().slice(0, 10)
    api.get<Minute[]>(`/api/feishu/minutes?start=${f(start)}&end=${f(end)}`)
      .then(l => alive && setList(l)).catch(e => { if (alive) { setErr((e as Error).message); setList([]) } })
    return () => { alive = false }
  }, [open, days])

  async function add(m: Minute) {
    setBusy(m.token)
    try {
      await api.post('/api/materials/feishu-minutes', { token: m.token, title: m.title, extract: true })
      setList(l => l && l.map(x => x.token === m.token ? { ...x, imported: true } : x))
      toast('妙记已导入，正在提炼选题')
      onImported()
      window.dispatchEvent(new Event('cw-refresh-status'))
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }

  return (
    <Dialog open={open} onClose={onClose} title="从飞书妙记导入" wide>
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="segmented" role="group" aria-label="时间范围">
          {[3, 7, 30].map(d => <button key={d} aria-pressed={days === d} onClick={() => setDays(d)}>最近 {d} 天</button>)}
        </div>
        <span className="small faint">只列你参与或创建的妙记</span>
      </div>
      {err && <div className="err">{err}</div>}
      {list === null ? (
        <div className="stack" style={{ padding: '6px 0 12px' }}>{[0, 1, 2].map(i => <div key={i} className="skeleton" style={{ height: 46 }} />)}</div>
      ) : list.length === 0 ? (
        <div className="empty"><b>这段时间没有妙记</b></div>
      ) : (
        <div className="card list-rows" style={{ marginBottom: 6 }}>
          {list.map(m => (
            <div key={m.token} className="list-row" style={{ cursor: 'default' }}>
              <span className="src-icon"><Ic.Wave size={16} /></span>
              <div className="grow">
                <div className="t">{m.title}</div>
                <div className="sub">{m.description.replace(/所有者:\s*\S+\s*/, '').replace(/开始时间:\s*/, '')}</div>
              </div>
              {m.imported ? <span className="pill ok"><Ic.Check size={12} /> 已导入</span> :
                <button className="btn sm" disabled={!!busy} onClick={() => add(m)}>{busy === m.token ? <Ic.Loader size={14} /> : <Ic.Plus size={14} />} 导入</button>}
            </div>
          ))}
        </div>
      )}
    </Dialog>
  )
}
