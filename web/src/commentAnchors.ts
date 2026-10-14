import { Extension } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

/* Comments are stored outside the article as quoted text (plus a little text
   before/after to tell repeats apart), the way a Feishu comment points at a
   selection. This plugin finds each quote in the editor and underlines it. */

export type Anchor = { id: number; quote: string; prefix?: string | null; suffix?: string | null }
type AnchorState = { anchors: Anchor[]; active: number | null; set: DecorationSet }
export const anchorKey = new PluginKey<AnchorState>('comment-anchors')

/** Plain text of the document with a position for every character; blocks are separated by \n. */
export function flatten(doc: PMNode): { text: string; pos: number[] } {
  let text = ''
  const pos: number[] = []
  doc.descendants((node, p) => {
    if (node.isText) {
      const t = node.text || ''
      for (let i = 0; i < t.length; i++) { text += t[i]; pos.push(p + i) }
    } else if (node.isBlock && text && !text.endsWith('\n')) {
      text += '\n'; pos.push(p)
    }
    return true
  })
  return { text, pos }
}

function score(text: string, at: number, a: Anchor): number {
  let s = 0
  if (a.prefix) { const before = text.slice(Math.max(0, at - a.prefix.length), at); for (let i = 1; i <= before.length && before[before.length - i] === a.prefix[a.prefix.length - i]; i++) s++ }
  if (a.suffix) { const end = at + a.quote.length; const after = text.slice(end, end + a.suffix.length); for (let i = 0; i < after.length && after[i] === a.suffix[i]; i++) s++ }
  return s
}

/** Where each anchor sits now: [from, to] in document positions, or null if the text is gone. */
export function locate(doc: PMNode, anchors: Anchor[]): Map<number, [number, number] | null> {
  const { text, pos } = flatten(doc)
  const out = new Map<number, [number, number] | null>()
  for (const a of anchors) {
    const q = (a.quote || '').trim()
    if (!q) { out.set(a.id, null); continue }
    let best = -1, bestScore = -1
    for (let i = text.indexOf(q); i >= 0; i = text.indexOf(q, i + 1)) {
      const sc = score(text, i, a)
      if (sc > bestScore) { best = i; bestScore = sc }
    }
    out.set(a.id, best < 0 ? null : [pos[best], pos[best + q.length - 1] + 1])
  }
  return out
}

function build(doc: PMNode, anchors: Anchor[], active: number | null): DecorationSet {
  const decos: Decoration[] = []
  for (const [id, range] of locate(doc, anchors)) {
    if (!range) continue
    decos.push(Decoration.inline(range[0], range[1], { class: 'cm-anchor' + (id === active ? ' active' : ''), 'data-cid': String(id) }, { cid: id }))
  }
  return DecorationSet.create(doc, decos)
}

export const CommentAnchors = Extension.create<{ onClick: { current: (id: number) => void } }>({
  name: 'commentAnchors',
  addOptions() { return { onClick: { current: () => {} } } },
  addProseMirrorPlugins() {
    const onClick = this.options.onClick
    return [new Plugin<AnchorState>({
      key: anchorKey,
      state: {
        init: (_, state): AnchorState => ({ anchors: [], active: null, set: DecorationSet.create(state.doc, []) }),
        apply(tr, value): AnchorState {
          const meta = tr.getMeta(anchorKey) as { anchors?: Anchor[]; active?: number | null } | undefined
          if (!meta && !tr.docChanged) return value
          const anchors = meta?.anchors ?? value.anchors
          const active = meta && 'active' in meta ? meta.active ?? null : value.active
          return { anchors, active, set: build(tr.doc, anchors, active) }
        },
      },
      props: {
        decorations(state) { return anchorKey.getState(state)?.set },
        handleClick(view, pos) {
          const found = anchorKey.getState(view.state)?.set.find(pos, pos) || []
          if (found.length) { onClick.current((found[0].spec as { cid: number }).cid); return false }
          return false
        },
      },
    })]
  },
})
