import { useEffect, useState } from 'react'
import { mergeAttributes, Node } from '@tiptap/core'
import { NodeViewWrapper, ReactNodeViewRenderer, type ReactNodeViewProps } from '@tiptap/react'
import { api, type DraftClip } from './api'
import * as Ic from './icons'

/* A video in the body. Markdown keeps it like a picture, ![说明](/api/clips/N);
   markdown-it renders that as <img src="/api/clips/N">, which this node claims
   before the Image extension does. While the server is still making the upload
   playable the block shows how far it is, then the player. */

export const clipId = (src: string) => Number(/\/api\/clips\/(\d+)/.exec(src)?.[1] || 0)
export const isVideoFile = (f: File) => f.type.startsWith('video/') || /\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(f.name)

function minutes(s: number) {
  const n = Math.round(s)
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`
}

function ClipView({ node, selected, editor, deleteNode }: ReactNodeViewProps) {
  const src = String(node.attrs.src || '')
  const id = clipId(src)
  const [info, setInfo] = useState<DraftClip | null>(null)
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    let stop = false
    let t = 0
    const load = () => api.get<DraftClip>(`/api/clips/${id}/info`).then(c => {
      if (stop) return
      setInfo(c)
      if (c.state === 'uploading' || c.state === 'processing') t = window.setTimeout(load, 2000)
    }).catch(() => { if (!stop) setMissing(true) })
    load()
    return () => { stop = true; window.clearTimeout(t) }
  }, [id])

  async function editLink() {
    const next = window.prompt('这段视频的观看链接（B 站、YouTube、发出去的 X 长文都行）。星球文章放不了视频，填了会在画面下面放一行「看视频」；不填就只放一帧画面。', info?.link || '')
    if (next === null) return
    try { setInfo(await api.patch<DraftClip>(`/api/clips/${id}`, { link: next.trim() })) } catch (e) { window.alert((e as Error).message) }
  }

  const state = missing ? 'missing' : info?.state
  return (
    <NodeViewWrapper className={'clip' + (selected ? ' selected' : '')} data-drag-handle>
      <div className="clip-bar" contentEditable={false}>
        <Ic.Film size={14} />
        <span className="clip-name">{info?.name || '视频'}</span>
        {info?.duration ? <span className="clip-meta">{minutes(info.duration)}</span> : null}
        {state === 'ready' && <a className="clip-act" href={src} download={info?.name} onMouseDown={e => e.stopPropagation()}>下载</a>}
        {state === 'ready' && editor.isEditable && (
          <button type="button" className="clip-act" title={info?.link || '星球文章放不了视频，填了观看链接会在画面下面放一行「看视频」'}
            onMouseDown={e => e.preventDefault()} onClick={editLink}>{info?.link ? '观看链接 ✓' : '加观看链接'}</button>
        )}
        {editor.isEditable && <button type="button" className="clip-act" onMouseDown={e => e.preventDefault()} onClick={() => deleteNode()}>从正文删掉</button>}
      </div>
      {state === 'ready'
        ? <video src={src} poster={`${src}/poster.jpg`} controls preload="metadata" playsInline />
        : (
          <div className={'clip-wait' + (state === 'failed' || state === 'missing' ? ' bad' : '')}>
            {state === 'failed' ? <><Ic.Alert size={16} /> {info?.error || '视频处理失败'}，删掉重新插一次</>
              : state === 'missing' ? <><Ic.Alert size={16} /> 找不到这段视频了，删掉重新插一次</>
              : state === 'uploading' ? <><Ic.Loader size={16} /> 还没传完（{Math.round(100 * (info?.received || 0) / Math.max(info?.size || 1, 1))}%）</>
              : <><Ic.Loader size={16} /> 正在转成 X 和浏览器都能放的格式，一般几秒到一两分钟</>}
          </div>
        )}
      {node.attrs.alt ? <div className="clip-cap">{String(node.attrs.alt)}</div> : null}
    </NodeViewWrapper>
  )
}

export const Clip = Node.create({
  name: 'clip',
  group: 'block',
  atom: true,
  draggable: true,
  selectable: true,
  addAttributes() {
    return { src: { default: null }, alt: { default: '' } }
  },
  parseHTML() {
    const attrs = (el: HTMLElement) => ({ src: el.getAttribute('src'), alt: el.getAttribute('alt') || '' })
    return [
      { tag: 'img[src^="/api/clips/"]', priority: 60, getAttrs: attrs },
      { tag: 'div[data-clip]', getAttrs: attrs },
    ]
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes, { 'data-clip': '' })]
  },
  addNodeView() {
    // clicks on the player's own controls stay with the player instead of selecting the block
    return ReactNodeViewRenderer(ClipView, {
      stopEvent: ({ event }) => (event.target as HTMLElement).tagName === 'VIDEO' && !event.type.startsWith('drag') && event.type !== 'drop',
    })
  },
})
