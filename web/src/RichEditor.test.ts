// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { Editor } from '@tiptap/core'
import { bodyExtensions, docToMarkdown, markdownToHtml } from './RichEditor'

/* Open a body in the editor and save it again: what comes back must be the same
   Markdown. Tables used to come back as one run-on paragraph (2026-11-02). */

const BODY = `## 先看价格

正文里有**加粗**和==高亮==，下面是一张图：

![](/api/images/12)

| 方案 | 分辨率 | 每秒积分 | 折合每秒 |
|---|---|---|---|
| 方案 A | 1080P | 64 | 1.95 元 |
| 方案 A 样片模式 | 480P | 9（原价 12） | 0.27 元 |
| **方案 B** | 720P | ==0.36 元== | 年付 \\| 季付 |

再放一段视频：

![录屏](/api/clips/3)

| 左 | 中 | 右 |
|:---|:---:|---:|
| 1 |  | 3 |

最后一段。
`

let editor: Editor | null = null
afterEach(() => { editor?.destroy(); editor = null })

function open(markdown: string): Editor {
  editor = new Editor({ element: document.createElement('div'), extensions: bodyExtensions(), content: markdownToHtml(markdown) })
  return editor
}

const save = (e: Editor) => docToMarkdown(e.getJSON())

function firstPos(e: Editor, type: string): number {
  let at = -1
  e.state.doc.descendants((node, pos) => {
    if (at < 0 && node.type.name === type) at = pos
    return at < 0
  })
  return at
}

describe('body round trip', () => {
  it('saves exactly what it opened', () => {
    expect(save(open(BODY))).toBe(BODY)
  })

  it('shows tables as tables, with header cells and alignment', () => {
    const e = open(BODY)
    const tables: string[] = []
    e.state.doc.descendants(node => { if (node.type.name === 'table') tables.push(node.textContent) })
    expect(tables).toHaveLength(2)
    expect(firstPos(e, 'tableHeader')).toBeGreaterThan(0)
    const html = e.getHTML()
    expect(html).toContain('<th')
    expect(html).toContain('text-align: right')
  })

  it('keeps a table after typing in a cell and adding a row', () => {
    const e = open(BODY)
    const cell = firstPos(e, 'tableCell')  // first body cell: 方案 A
    e.chain().setTextSelection(cell + 2).insertContent('新').addRowAfter().run()
    const md = save(e)
    expect(md).toContain('| 新方案 A | 1080P | 64 | 1.95 元 |\n|  |  |  |  |\n| 方案 A 样片模式 |')
    expect(md).toContain('|---|---|---|---|')
  })

  it('writes a new table as Markdown and reads it back the same', () => {
    const e = open('开头\n')
    e.chain().setTextSelection(e.state.doc.content.size - 1).insertTable({ rows: 2, cols: 2, withHeaderRow: true }).run()
    const md = save(e)
    expect(md).toBe('开头\n\n|  |  |\n|---|---|\n|  |  |\n')
    expect(save(open(md))).toBe(md)
  })

  it('keeps a cell on one line and its pipes escaped', () => {
    const e = open('| a | b |\n|---|---|\n| x | y |\n')
    const cell = firstPos(e, 'tableCell')
    e.chain().setTextSelection(cell + 3).setHardBreak().insertContent('第二行 | 竖线').run()
    expect(save(e)).toBe('| a | b |\n|---|---|\n| x 第二行 \\| 竖线 | y |\n')
  })
})
