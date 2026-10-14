import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import * as Ic from './icons'

// ------------------------------------------------------------------ toast

type Toast = { id: number; kind: 'ok' | 'error' | 'info'; text: string }
const ToastCtx = createContext<(text: string, kind?: Toast['kind']) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((text: string, kind: Toast['kind'] = 'ok') => {
    const id = Date.now() + Math.random()
    setItems(list => [...list, { id, kind, text }])
    window.setTimeout(() => setItems(list => list.filter(t => t.id !== id)), kind === 'error' ? 6000 : 3200)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts">
        {items.map(t => (
          <div key={t.id} className={'toast ' + t.kind} role={t.kind === 'error' ? 'alert' : 'status'}>
            {t.kind === 'error' ? <Ic.Alert size={16} /> : <Ic.Check size={16} />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
export const useToast = () => useContext(ToastCtx)

// ------------------------------------------------------------------ dialog

export function Dialog({ open, title, onClose, children, footer, wide }: {
  open: boolean; title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog ref={ref} className="dlg" style={wide ? { width: 'min(720px, calc(100vw - 32px))' } : undefined}
      onClose={onClose} onClick={e => { if (e.target === ref.current) onClose() }}>
      {open && (
        <div className="dlg-in">
          <div className="dlg-head">
            <h2>{title}</h2>
            <button className="btn ghost icon sm x" aria-label="关闭" onClick={onClose}><Ic.Close size={16} /></button>
          </div>
          <div className="dlg-body">{children}</div>
          {footer && <div className="dlg-foot">{footer}</div>}
        </div>
      )}
    </dialog>
  )
}

// ------------------------------------------------------------------ small pieces

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <b>{title}</b>
      {children && <div className="small">{children}</div>}
    </div>
  )
}

export function PlatformPill({ platform }: { platform: string }) {
  if (platform === 'x' || platform === 'X') return <span className="pill x"><Ic.XLogo size={11} /> X</span>
  return <span className="pill wechat"><Ic.Doc size={12} /> 长文</span>
}

export function PlatformIcon({ platform }: { platform: string }) {
  return platform === 'x'
    ? <span className="src-icon x"><Ic.XLogo size={14} /></span>
    : <span className="src-icon wechat"><Ic.Doc size={16} /></span>
}

export function SourceIcon({ source }: { source: string }) {
  const icon = source === 'audio' ? <Ic.Mic size={16} /> : source === 'feishu_minutes' ? <Ic.Wave size={16} />
    : source === 'feishu_doc' || source === 'feishu_table' ? <Ic.Feishu size={16} /> : <Ic.Note size={16} />
  return <span className="src-icon">{icon}</span>
}

/** Textarea that grows with its content. */
export function useAutosize(value: string) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = el.scrollHeight + 'px'
  }, [value])
  return ref
}

export function AutoTextarea({ inputRef, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  value: string; inputRef?: React.RefObject<HTMLTextAreaElement | null>
}) {
  const ref = useAutosize(props.value)
  return <textarea rows={1} {...props} ref={el => { ref.current = el; if (inputRef) inputRef.current = el }} />
}

// ------------------------------------------------------------------ time

export function ago(ts: number): string {
  const s = Date.now() / 1000 - ts
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  const d = new Date(ts * 1000)
  const now = new Date()
  const hm = d.toTimeString().slice(0, 5)
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`
  const y = new Date(now); y.setDate(now.getDate() - 1)
  if (d.toDateString() === y.toDateString()) return `昨天 ${hm}`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

export function greeting(): string {
  const h = new Date().getHours()
  if (h < 5) return '夜深了'
  if (h < 11) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
}

export function todayLabel(): string {
  const d = new Date()
  const w = '日一二三四五六'[d.getDay()]
  return `${d.getMonth() + 1}月${d.getDate()}日 星期${w}`
}

/** Poll a loader while the component is mounted and the tab is visible. */
export function usePoll(fn: () => void, ms: number, deps: unknown[] = []) {
  useEffect(() => {
    fn()
    const t = window.setInterval(() => { if (document.visibilityState === 'visible') fn() }, ms)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
}
