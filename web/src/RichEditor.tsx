import { useEffect, useRef } from 'react'
import { useEditor, EditorContent, useEditorState, type Editor as TiptapEditor } from '@tiptap/react'
import { BubbleMenu } from '@tiptap/react/menus'
import { anchorKey, CommentAnchors, type Anchor } from './commentAnchors'
import StarterKit from '@tiptap/starter-kit'
import Highlight from '@tiptap/extension-highlight'
import Image from '@tiptap/extension-image'
import Placeholder from '@tiptap/extension-placeholder'
import MarkdownIt from 'markdown-it'
import markPlugin from 'markdown-it-mark'
import type { JSONContent } from '@tiptap/core'
import * as Ic from './icons'

/* The draft is stored as Markdown (## 标题, **加粗**, ==高亮==, ![](/api/images/N)).
   The editor shows it as formatted text with the pictures in place and writes
   Markdown back on every change. */

const md = new MarkdownIt('commonmark', { html: false, linkify: false }).enable('table').use(markPlugin)

export function markdownToHtml(markdown: string): string {
  // the title is its own field, so the body's top level is ##
  return md.render(normalizeEmphasis(markdown).replace(/^# /gm, '## '))
}

/* Same rule as textutil.normalize_emphasis on the server: CommonMark won't close
   `**…。**放` or open `放**「…`, so punctuation at the edge of a bold/highlight
   that touches a letter moves just outside it. */
const PUNCT = '。！？，；：、“”‘’「」『』（）《》〈〉【】…—～·.,!?;:\'"()[]<>'
const isSpace = (c: string) => /\s/.test(c)

function fixPairs(line: string, delim: string): string {
  const pos: number[] = []
  for (let i = line.indexOf(delim); i >= 0; i = line.indexOf(delim, i + delim.length)) pos.push(i)
  if (pos.length < 2) return line
  const n = delim.length
  let out = '', last = 0
  for (let k = 0; k + 1 < pos.length; k += 2) {
    const o = pos[k], c = pos[k + 1]
    let inner = line.slice(o + n, c)
    const before = o > 0 ? line[o - 1] : ' '
    const after = c + n < line.length ? line[c + n] : ' '
    let lead = '', trail = ''
    if (!isSpace(before) && !PUNCT.includes(before)) {
      let j = 0
      while (j < inner.length && PUNCT.includes(inner[j])) j++
      lead = inner.slice(0, j); inner = inner.slice(j)
    }
    if (!isSpace(after) && !PUNCT.includes(after)) {
      let j = inner.length
      while (j > 0 && PUNCT.includes(inner[j - 1])) j--
      trail = inner.slice(j); inner = inner.slice(0, j)
    }
    if (!inner) continue
    out += line.slice(last, o) + lead + delim + inner + delim + trail
    last = c + n
  }
  return out + line.slice(last)
}

export function normalizeEmphasis(markdown: string): string {
  let fence = false
  return markdown.split('\n').map(line => {
    if (line.trimStart().startsWith('```')) fence = !fence
    if (fence || line.includes('`')) return line
    return fixPairs(fixPairs(line, '**'), '==')
  }).join('\n')
}

// ------------------------------------------------------------------ JSON → Markdown

type Mark = { type: string; attrs?: Record<string, unknown> }
const ORDER = ['link', 'bold', 'highlight', 'italic', 'code']

function open(m: Mark): string {
  if (m.type === 'bold') return '**'
  if (m.type === 'highlight') return '=='
  if (m.type === 'italic') return '*'
  if (m.type === 'code') return '`'
  if (m.type === 'link') return '['
  return ''
}
function close(m: Mark): string {
  if (m.type === 'link') return `](${String(m.attrs?.href || '')})`
  return open(m)
}

function escapeText(t: string): string {
  return t.replace(/([\\`*_[\]])/g, '\\$1').replace(/==/g, '\\=\\=')
}

function inline(nodes: JSONContent[] | undefined): string {
  let out = ''
  let stack: Mark[] = []
  const closeTo = (n: number) => { while (stack.length > n) out += close(stack.pop()!) }
  for (const node of nodes || []) {
    if (node.type === 'hardBreak') { closeTo(0); out += '\\\n'; continue }
    if (node.type !== 'text') continue
    const marks = ((node.marks || []) as Mark[]).filter(m => ORDER.includes(m.type))
      .sort((a, b) => ORDER.indexOf(a.type) - ORDER.indexOf(b.type))
    // keep the common prefix of open marks, close the rest, open the new ones
    let keep = 0
    while (keep < stack.length && keep < marks.length && stack[keep].type === marks[keep].type
      && JSON.stringify(stack[keep].attrs || {}) === JSON.stringify(marks[keep].attrs || {})) keep++
    closeTo(keep)
    for (const m of marks.slice(keep)) { out += open(m); stack.push(m) }
    const text = node.text || ''
    out += marks.some(m => m.type === 'code') ? text : escapeText(text)
  }
  closeTo(0)
  stack = []
  return out
}

function lineStart(text: string): string {
  // stop a paragraph from turning into a heading, quote or list
  return text.replace(/^(#{1,6}\s|>|[-+]\s)/gm, m => '\\' + m).replace(/^(\d+)([.)])(\s)/gm, '$1\\$2$3')
}

function block(node: JSONContent, indent = ''): string {
  const kids = node.content || []
  switch (node.type) {
    case 'paragraph': return indent + lineStart(inline(kids)).replace(/\n/g, '\n' + indent)
    case 'heading': return '#'.repeat(Number(node.attrs?.level || 2)) + ' ' + inline(kids)
    case 'blockquote': return kids.map(k => block(k)).join('\n\n').split('\n').map(l => (l ? '> ' + l : '>')).join('\n')
    case 'bulletList':
    case 'orderedList': {
      let n = Number(node.attrs?.start || 1)
      return kids.map(item => {
        const marker = node.type === 'bulletList' ? '- ' : `${n++}. `
        const parts = (item.content || []).map(k => block(k))
        const pad = ' '.repeat(marker.length)
        return marker + parts.join('\n\n').split('\n').map((l, i) => (i === 0 || !l ? l : pad + l)).join('\n')
      }).join('\n')
    }
    case 'horizontalRule': return '---'
    case 'image': return `![${String(node.attrs?.alt || '')}](${String(node.attrs?.src || '')})`
    case 'codeBlock': return '```' + String(node.attrs?.language || '') + '\n' + kids.map(k => k.text || '').join('') + '\n```'
    default: return inline(kids)
  }
}

export function docToMarkdown(doc: JSONContent): string {
  return normalizeEmphasis((doc.content || []).map(n => block(n)).filter(s => s.trim() !== '').join('\n\n') + '\n')
}

// ------------------------------------------------------------------ editor

export type Selection = { quote: string; prefix: string; suffix: string }

export function RichEditor({ value, onChange, onFiles, placeholder, editorRef, anchors, activeAnchor, onAnchorClick, onComment, editable = true }: {
  value: string
  onChange: (markdown: string) => void
  onFiles: (files: File[]) => Promise<number[]>
  placeholder?: string
  editorRef?: React.MutableRefObject<TiptapEditor | null>
  anchors?: Anchor[]
  activeAnchor?: number | null
  onAnchorClick?: (id: number) => void
  onComment?: (sel: Selection) => void
  editable?: boolean
}) {
  const lastEmitted = useRef(value)
  const onFilesRef = useRef(onFiles)
  onFilesRef.current = onFiles
  const clickRef = useRef<(id: number) => void>(() => {})
  clickRef.current = id => onAnchorClick?.(id)

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [2, 3, 4] }, underline: false, strike: false,
        link: { openOnClick: false, autolink: true } }),
      Highlight.configure({ multicolor: false }),
      Image.configure({ inline: false, allowBase64: false }),
      Placeholder.configure({ placeholder: placeholder || '从这里开始写' }),
      CommentAnchors.configure({ onClick: clickRef }),
    ],
    editable,
    content: markdownToHtml(value),
    editorProps: {
      attributes: { class: 'rich-body', spellcheck: 'false' },
      handlePaste: (view, event) => {
        const files = [...(event.clipboardData?.files || [])].filter(f => f.type.startsWith('image/'))
        if (!files.length) return false
        event.preventDefault()
        insertUploaded(view.state.selection.from, files)
        return true
      },
      handleDrop: (view, event) => {
        const files = [...(event.dataTransfer?.files || [])].filter(f => f.type.startsWith('image/'))
        if (!files.length) return false
        event.preventDefault()
        const pos = view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos ?? view.state.selection.from
        insertUploaded(pos, files)
        return true
      },
    },
    onUpdate: ({ editor }) => {
      const next = docToMarkdown(editor.getJSON())
      lastEmitted.current = next
      onChange(next)
    },
  })

  async function insertUploaded(pos: number, files: File[]) {
    const ids = await onFilesRef.current(files)
    if (!editor || !ids.length) return
    editor.chain().focus().insertContentAt(pos, ids.map(id => ({ type: 'image', attrs: { src: `/api/images/${id}` } }))).run()
  }

  // outside changes (restore a version, AI draft arriving) replace the content
  useEffect(() => {
    if (!editor || value === lastEmitted.current) return
    lastEmitted.current = value
    editor.commands.setContent(markdownToHtml(value), { emitUpdate: false })
  }, [value, editor])

  useEffect(() => { if (editorRef) editorRef.current = editor }, [editor, editorRef])
  useEffect(() => { editor?.setEditable(editable) }, [editor, editable])

  // comment highlights follow the comment list and the selected thread
  useEffect(() => {
    if (!editor) return
    editor.view.dispatch(editor.state.tr.setMeta(anchorKey, { anchors: anchors || [], active: activeAnchor ?? null }))
  }, [editor, anchors, activeAnchor])

  function commentOnSelection() {
    if (!editor || !onComment) return
    const { from, to } = editor.state.selection
    const doc = editor.state.doc
    const quote = doc.textBetween(from, to, '\n').trim().slice(0, 500)
    if (!quote) return
    onComment({ quote, prefix: doc.textBetween(Math.max(0, from - 30), from, '\n'), suffix: doc.textBetween(to, Math.min(doc.content.size, to + 30), '\n') })
  }

  if (!editor) return null
  return (
    <div className="rich">
      {editable && <Toolbar editor={editor} onPick={files => insertUploaded(editor.state.selection.from, files)} />}
      <BubbleMenu editor={editor} className="bubble" shouldShow={({ editor: e, from, to }) => from !== to && !e.isActive('image')}>
        {editable && <>
          <button type="button" onMouseDown={e => e.preventDefault()} onClick={() => editor.chain().focus().toggleBold().run()} aria-label="加粗"><b>B</b></button>
          <button type="button" onMouseDown={e => e.preventDefault()} onClick={() => editor.chain().focus().toggleHighlight().run()} aria-label="高亮"><span className="hl">高亮</span></button>
          <span className="sep" />
        </>}
        {onComment && <button type="button" className="cm" onMouseDown={e => e.preventDefault()} onClick={commentOnSelection}><Ic.Note size={14} /> 批注</button>}
      </BubbleMenu>
      <EditorContent editor={editor} />
    </div>
  )
}

function Toolbar({ editor, onPick }: { editor: TiptapEditor; onPick: (files: File[]) => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const s = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      h2: e.isActive('heading', { level: 2 }), h3: e.isActive('heading', { level: 3 }), h4: e.isActive('heading', { level: 4 }),
      bold: e.isActive('bold'), mark: e.isActive('highlight'), quote: e.isActive('blockquote'),
      ul: e.isActive('bulletList'), ol: e.isActive('orderedList'),
      undo: e.can().undo(), redo: e.can().redo(),
    }),
  })
  const c = () => editor.chain().focus()
  const B = ({ on, label, title, run, children }: { on?: boolean; label: string; title?: string; run: () => void; children: React.ReactNode }) => (
    <button type="button" className={'tb' + (on ? ' on' : '')} aria-pressed={!!on} aria-label={label} title={title || label}
      onMouseDown={e => e.preventDefault()} onClick={run}>{children}</button>
  )
  return (
    <div className="rich-toolbar" role="toolbar" aria-label="排版">
      <B label="正文" on={!s.h2 && !s.h3 && !s.h4 && !s.quote} run={() => c().setParagraph().run()}>正文</B>
      <B label="一级标题" on={s.h2} run={() => c().toggleHeading({ level: 2 }).run()}>标题1</B>
      <B label="二级标题" on={s.h3} run={() => c().toggleHeading({ level: 3 }).run()}>标题2</B>
      <B label="三级标题" on={s.h4} run={() => c().toggleHeading({ level: 4 }).run()}>标题3</B>
      <span className="sep" />
      <B label="加粗" title="加粗（⌘B）" on={s.bold} run={() => c().toggleBold().run()}><b>B</b></B>
      <B label="高亮" title="高亮（⌘⇧H）" on={s.mark} run={() => c().toggleHighlight().run()}><span className="hl">高亮</span></B>
      <span className="sep" />
      <B label="引用" on={s.quote} run={() => c().toggleBlockquote().run()}>引用</B>
      <B label="无序列表" on={s.ul} run={() => c().toggleBulletList().run()}>• 列表</B>
      <B label="有序列表" on={s.ol} run={() => c().toggleOrderedList().run()}>1. 列表</B>
      <B label="分割线" run={() => c().setHorizontalRule().run()}>—</B>
      <B label="插入图片" run={() => fileRef.current?.click()}><Ic.Image size={15} /></B>
      <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden
        onChange={e => { onPick([...(e.target.files || [])]); e.target.value = '' }} />
      <span className="sep" />
      <B label="撤销" run={() => c().undo().run()}><Ic.Undo size={15} /></B>
      <B label="重做" run={() => c().redo().run()}><span style={{ display: 'inline-flex', transform: 'scaleX(-1)' }}><Ic.Undo size={15} /></span></B>
    </div>
  )
}
