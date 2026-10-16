// Small inline stroke icons (24px grid). Decorative unless given a label.
import type { ReactNode } from 'react'

type P = { size?: number; className?: string; label?: string }

function I({ size = 18, className, label, children }: P & { children: ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" className={className}
      aria-hidden={label ? undefined : true} role={label ? 'img' : undefined} aria-label={label}>
      {children}
    </svg>
  )
}

export const Sun = (p: P) => <I {...p}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></I>
export const Inbox = (p: P) => <I {...p}><path d="M22 12h-6l-2 3h-4l-2-3H2" /><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1z" /></I>
export const Bulb = (p: P) => <I {...p}><path d="M9 18h6M10 22h4" /><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z" /></I>
export const Doc = (p: P) => <I {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></I>
export const Send = (p: P) => <I {...p}><path d="m22 2-7 20-4-9-9-4z" /><path d="M22 2 11 13" /></I>
export const Gear = (p: P) => <I {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></I>
export const Mic = (p: P) => <I {...p}><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4M8 22h8" /></I>
export const Upload = (p: P) => <I {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" /></I>
export const LinkI = (p: P) => <I {...p}><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" /><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" /></I>
export const Wave = (p: P) => <I {...p}><path d="M2 10v4M6 6v12M10 3v18M14 8v8M18 5v14M22 10v4" /></I>
export const Plus = (p: P) => <I {...p}><path d="M12 5v14M5 12h14" /></I>
export const Star = (p: P & { filled?: boolean }) => <I {...p}><path fill={p.filled ? 'currentColor' : 'none'} d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z" /></I>
export const Trash = (p: P) => <I {...p}><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /></I>
export const History = (p: P) => <I {...p}><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5M12 7v5l3 2" /></I>
export const Back = (p: P) => <I {...p}><path d="m15 18-6-6 6-6" /></I>
export const Right = (p: P) => <I {...p}><path d="m9 18 6-6-6-6" /></I>
export const Down = (p: P) => <I {...p}><path d="m6 9 6 6 6-6" /></I>
export const Close = (p: P) => <I {...p}><path d="M18 6 6 18M6 6l12 12" /></I>
export const Check = (p: P) => <I {...p}><path d="M20 6 9 17l-5-5" /></I>
export const Alert = (p: P) => <I {...p}><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></I>
export const Sparkle = (p: P) => <I {...p}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" /></I>
export const Loader = (p: P) => <I {...p} className={(p.className || '') + ' spin'}><path d="M21 12a9 9 0 1 1-6.2-8.6" /></I>
export const Eye = (p: P) => <I {...p}><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></I>
export const Note = (p: P) => <I {...p}><path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" /></I>
export const Pen = Note
export const Stop = (p: P) => <I {...p}><rect x="6" y="6" width="12" height="12" rx="2" /></I>
export const External = (p: P) => <I {...p}><path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /></I>
export const Key = (p: P) => <I {...p}><circle cx="7.5" cy="15.5" r="5.5" /><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3" /></I>
export const Cpu = (p: P) => <I {...p}><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" /></I>
export const Image = (p: P) => <I {...p}><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="9" cy="9" r="2" /><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21" /></I>
export const Logout = (p: P) => <I {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></I>
export const Feishu = (p: P) => <I {...p}><path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5z" /><path d="M8 11.5 12 14l4-2.5" /></I>
export const Waves = Wave
export const Film = (p: P) => <I {...p}><rect x="5" y="2" width="14" height="20" rx="2.5" /><path d="m10 9 5 3-5 3z" /></I>
export const Play = (p: P) => <I {...p}><path d="m7 4 13 8-13 8z" /></I>
export const Pause = (p: P) => <I {...p}><path d="M8 5v14M16 5v14" /></I>
export const Download = (p: P) => <I {...p}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" /></I>
export const Up = (p: P) => <I {...p}><path d="m18 15-6-6-6 6" /></I>
export const Undo = (p: P) => <I {...p}><path d="M3 7v6h6" /><path d="M21 17a9 9 0 0 0-15-6.7L3 13" /></I>

export const XLogo = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M17.8 3h3.1l-6.8 7.7L22 21h-6.2l-4.9-6.3L5.3 21H2.2l7.3-8.3L2 3h6.4l4.4 5.8zm-1.1 16.2h1.7L7.4 4.7H5.6z" />
  </svg>
)
export const WechatLogo = ({ size = 16 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M9 3.5C4.9 3.5 1.5 6.3 1.5 9.8c0 2 1.1 3.7 2.8 4.9l-.7 2.2 2.6-1.3c.9.3 1.8.4 2.8.4h.4a5.7 5.7 0 0 1-.2-1.5c0-3.4 3.2-6.1 7.2-6.1h.5C16.2 5.6 12.9 3.5 9 3.5zM6.5 8.6a1 1 0 1 1 0-2 1 1 0 0 1 0 2zm5 0a1 1 0 1 1 0-2 1 1 0 0 1 0 2z" />
    <path d="M22.5 14.4c0-2.9-2.9-5.2-6.4-5.2s-6.4 2.3-6.4 5.2 2.9 5.2 6.4 5.2c.8 0 1.5-.1 2.2-.3l2.1 1.1-.6-1.8c1.6-1 2.7-2.5 2.7-4.2zm-8.6-.9a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8zm4.4 0a.9.9 0 1 1 0-1.8.9.9 0 0 1 0 1.8z" />
  </svg>
)
