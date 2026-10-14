import { useEffect, useRef, useState } from 'react'
import { api, type CaseSearch, type Draft, type DraftImage, type Status } from '../api'
import { go } from '../App'
import { ago, AutoTextarea, Dialog, usePoll, useToast } from '../ui'
import * as Ic from '../icons'

export const imgUrl = (id: number) => `/api/images/${id}`
const REF = /\/api\/images\/(\d+)/g

export function referencedImages(body: string): number[] {
  const seen: number[] = []
  for (const m of body.matchAll(REF)) { const n = Number(m[1]); if (!seen.includes(n)) seen.push(n) }
  return seen
}

/** Text in 【】 that still needs the author's own words before it goes out. */
export function placeholders(body: string): string[] {
  return [...body.matchAll(/【[^】\n]{0,80}】/g)].map(m => m[0])
}

export async function uploadImages(draftId: number, files: File[]): Promise<DraftImage[]> {
  const f = new FormData()
  files.forEach(file => f.append('files', file))
  return api.upload<DraftImage[]>(`/api/drafts/${draftId}/images`, f)
}

function kb(bytes: number) {
  return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)}MB` : `${Math.round(bytes / 1024)}KB`
}

type CoverPatch = Partial<Pick<Draft, 'cover_wechat' | 'cover_x' | 'cover_crop'>>

/** Side panel for long-form drafts: covers for both platforms, pictures, 摘要 and 阅读原文. */
export function MediaPanel({ draft, body, summary, sourceUrl, onSummary, onSourceUrl, onCovers, onInsert, onUploaded, onRemoved }: {
  draft: Draft; body: string; summary: string; sourceUrl: string
  onSummary: (v: string) => void; onSourceUrl: (v: string) => void
  onCovers: (patch: CoverPatch) => void; onInsert: (id: number) => void
  onUploaded: (imgs: DraftImage[]) => void; onRemoved: (id: number) => void
}) {
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const imgs = draft.images || []
  const used = referencedImages(body)
  const wx = imgs.find(i => i.id === draft.cover_wechat)
  const xc = imgs.find(i => i.id === draft.cover_x)
  const sq = draft.cover_crop?.square_x ?? 0

  async function upload(files: File[]) {
    if (!files.length) return
    setBusy(true)
    try { const added = await uploadImages(draft.id, files); onUploaded(added); toast(`传好了 ${added.length} 张`) }
    catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  async function remove(id: number) {
    try { await api.del(`/api/images/${id}`); onRemoved(id) } catch (e) { toast((e as Error).message, 'error') }
  }

  return (
    <div className="stack media-panel">
      <section>
        <div className="mp-head"><Ic.WechatLogo size={13} /> 公众号封面 <span className="faint small">2.35:1，另裁一块 1:1</span></div>
        {wx ? (
          <>
            <div className="cover-frame r235"><img src={imgUrl(wx.id)} alt="公众号封面" /></div>
            <div className="sq-row">
              <div className="cover-frame r11"><img src={imgUrl(wx.id)} alt="方形裁剪" style={{ objectPosition: `${sq * 100}% 50%` }} /></div>
              <div className="grow">
                <div className="small muted">方形那块用在分享卡片和消息列表的小图上，拖一下选位置。</div>
                {wx.width > wx.height && (
                  <input type="range" min={0} max={1} step={0.01} value={sq} aria-label="方形裁剪位置"
                    onChange={e => onCovers({ cover_crop: { square_x: Number(e.target.value) } })} />
                )}
                <div className="row small">
                  {([[0, '靠左'], [0.5, '居中'], [1, '靠右']] as const).map(([v, l]) => (
                    <button key={l} className="btn ghost sm" aria-pressed={Math.abs(sq - v) < 0.01} onClick={() => onCovers({ cover_crop: { square_x: v } })}>{l}</button>
                  ))}
                </div>
              </div>
            </div>
          </>
        ) : <div className="cover-empty">还没选。在下面的图片里点“公众号封面”；不选就用设置里的默认封面。</div>}
      </section>

      <section>
        <div className="mp-head"><Ic.XLogo size={11} /> X 长文封面 <span className="faint small">5:2</span></div>
        {xc ? <div className="cover-frame r52"><img src={imgUrl(xc.id)} alt="X 长文封面" /></div>
          : <div className="cover-empty">{wx ? '没单独选，会用公众号封面（X 会按 5:2 裁一下）。' : '还没选。'}</div>}
      </section>

      <section>
        <div className="mp-head"><Ic.Image size={14} /> 这篇的图片 <span className="faint small">{imgs.length} 张</span>
          <button className="btn sm" style={{ marginLeft: 'auto' }} disabled={busy} onClick={() => fileRef.current?.click()}>
            {busy ? <Ic.Loader size={14} /> : <Ic.Upload size={14} />} 上传
          </button>
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden
            onChange={e => { upload([...(e.target.files || [])]); e.target.value = '' }} />
        </div>
        {imgs.length === 0 ? <div className="cover-empty">正文里直接粘贴或拖进图片也行，会自动传上来、插在光标处。</div> : (
          <div className="img-grid">
            {imgs.map(im => {
              const n = used.indexOf(im.id)
              return (
                <div key={im.id} className="img-tile">
                  <div className="thumb"><img src={imgUrl(im.id)} alt={im.name} loading="lazy" /></div>
                  <div className="meta">
                    <div className="tags">
                      {draft.cover_wechat === im.id && <span className="pill wechat">公众号封面</span>}
                      {draft.cover_x === im.id && <span className="pill x">X 封面</span>}
                      {n >= 0 && <span className="pill accent">正文第 {n + 1} 张</span>}
                    </div>
                    <div className="faint small">{im.width}×{im.height} · {kb(im.bytes)}</div>
                    <div className="acts">
                      <button className="btn ghost sm" onClick={() => onCovers({ cover_wechat: draft.cover_wechat === im.id ? null : im.id })}>
                        {draft.cover_wechat === im.id ? '取消公众号封面' : '公众号封面'}</button>
                      <button className="btn ghost sm" onClick={() => onCovers({ cover_x: draft.cover_x === im.id ? null : im.id })}>
                        {draft.cover_x === im.id ? '取消 X 封面' : 'X 封面'}</button>
                      <button className="btn ghost sm" onClick={() => onInsert(im.id)}>插入正文</button>
                      {n < 0 && <button className="btn ghost sm danger" aria-label="删除这张图" onClick={() => remove(im.id)}><Ic.Trash size={13} /></button>}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </section>

      <section>
        <div className="mp-head"><Ic.Note size={14} /> 摘要 <span className="faint small">{summary.length}/120，空着就取正文开头</span></div>
        <AutoTextarea className="textarea" maxLength={120} value={summary} onChange={e => onSummary(e.target.value)} placeholder="分享卡片下面那行小字" />
      </section>
      <section>
        <div className="mp-head"><Ic.LinkI size={14} /> 阅读原文 <span className="faint small">公众号文末的链接，可不填</span></div>
        <input className="input" value={sourceUrl} onChange={e => onSourceUrl(e.target.value)} placeholder="https://" inputMode="url" />
      </section>
    </div>
  )
}

/** How the article shows up in the 公众号 message list. */
export function FeedCard({ draft, title, summary, body }: { draft: Draft; title: string; summary: string; body: string }) {
  const cover = draft.cover_wechat
  const plain = body.replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/^#+\s*/gm, '').replace(/\*\*/g, '').replace(/\s+/g, ' ').trim()
  return (
    <div className="feed-card">
      <div className="cover-frame r235">{cover ? <img src={imgUrl(cover)} alt="" /> : <div className="cover-none">默认封面</div>}</div>
      <div className="ft">{title || '标题'}</div>
      <div className="fd">{(summary.trim() || plain).slice(0, 54)}</div>
    </div>
  )
}

type Target = 'wechat' | 'x_article' | 'zsxq'

export function PublishLongform({ open, draft, body, status, onClose, onSent }: {
  open: boolean; draft: Draft; body: string; status: Status | null; onClose: () => void; onSent: (target: Target) => void
}) {
  const toast = useToast()
  const [target, setTarget] = useState<Target>('wechat')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [xName, setXName] = useState<string | null>(null)
  useEffect(() => {
    if (!open) return
    setErr('')
    api.get<{ x_account: { username?: string; subscription_type?: string } | null }>('/api/settings')
      .then(s => setXName(s.x_account ? `@${s.x_account.username}${s.x_account.subscription_type ? ` · ${s.x_account.subscription_type}` : ''}` : null)).catch(() => {})
  }, [open])

  const pics = referencedImages(body).length
  const holes = placeholders(body)
  const wxCover = draft.cover_wechat ? 'this' : status?.wechat_cover ? 'default' : 'none'
  const xCover = draft.cover_x ? 'x' : draft.cover_wechat ? 'wechat' : 'none'
  const needsMedia = pics > 0 || xCover !== 'none'
  const wxReady = !!status?.wechat_configured && wxCover !== 'none'
  const xReady = !!status?.x_connected && (!needsMedia || !!status?.x_media) && !!draft.title.trim()
  const zsxqReady = !!status?.zsxq_connected && !!status?.zsxq_group && !!draft.title.trim() && draft.title.trim().length <= 60
  const ready = target === 'wechat' ? wxReady : target === 'zsxq' ? zsxqReady : xReady

  async function send() {
    setBusy(true); setErr('')
    try {
      await api.post(`/api/drafts/${draft.id}/publish`, { confirm: true, target })
      toast(target === 'wechat' ? '已推到公众号草稿箱，去手机上检查后点发表' : target === 'zsxq' ? '已存进星球的文章草稿箱，去星球里检查后点发布' : '已建好 X 长文草稿，去 X 上检查后点发布')
      onSent(target)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const Line = ({ ok, warn, label, children }: { ok?: boolean; warn?: boolean; label: string; children: React.ReactNode }) => (
    <div className="check-line">
      <span className={'ck ' + (ok ? 'ok' : warn ? 'warn' : 'bad')}>{ok ? <Ic.Check size={13} /> : <Ic.Alert size={13} />}</span>
      <span className="lb">{label}</span><span className="vl">{children}</span>
    </div>
  )

  return (
    <Dialog open={open} onClose={onClose} title="推到草稿箱" wide footer={<>
      <button className="btn" onClick={onClose} disabled={busy}>再改改</button>
      <button className="btn primary" onClick={send} disabled={busy || !ready}>{busy ? <Ic.Loader size={15} /> : <Ic.Send size={15} />}
        {target === 'wechat' ? ' 推到公众号草稿箱' : target === 'zsxq' ? ' 存进星球草稿箱' : ' 推到 X 长文草稿'}</button>
    </>}>
      <div className="stack">
        <div className="segmented" role="group" aria-label="发到哪里">
          <button aria-pressed={target === 'wechat'} onClick={() => setTarget('wechat')}><Ic.WechatLogo size={13} /> 公众号草稿箱</button>
          <button aria-pressed={target === 'x_article'} onClick={() => setTarget('x_article')}><Ic.XLogo size={11} /> X 长文草稿</button>
          <button aria-pressed={target === 'zsxq'} onClick={() => setTarget('zsxq')}>星 星球草稿箱</button>
        </div>
        {target === 'zsxq' ? (
          <div className="checks">
            <Line ok={!!status?.zsxq_connected && !!status?.zsxq_group} label="知识星球">{!status?.zsxq_connected ? <a onClick={() => go('settings')}>还没连接，去设置里扫码</a> : !status?.zsxq_group ? <a onClick={() => go('settings')}>还没填星球编号，去设置里填</a> : `已连接，存进「${status.zsxq_group}」的文章草稿箱`}</Line>
            <Line ok={!!draft.title.trim() && draft.title.trim().length <= 60} label="标题">{draft.title.trim().length > 60 ? `星球标题最多 60 个字，现在 ${draft.title.trim().length} 个` : draft.title.trim() || '要有标题'}</Line>
            <Line ok label="正文图片">{pics ? `${pics} 张，推的时候自动传到星球` : '没有'}</Line>
            <Line ok label="排版">标题、加粗、列表、引用都带过去；星球文章没有高亮，高亮会变成加粗</Line>
            <div className="callout info"><Ic.Alert size={15} /><span>只存进星球的「我的文章-草稿箱」，不会发布。再推一次会更新同一篇草稿，不会多出一篇。去星球网页或 App 里看一遍再点发布。</span></div>
          </div>
        ) : target === 'wechat' ? (
          <div className="checks">
            <Line ok={!!status?.wechat_configured} label="公众号">{status?.wechat_configured ? '已连接' : <a onClick={() => go('settings')}>还没配好，去设置</a>}</Line>
            <Line ok={wxCover !== 'none'} warn={wxCover === 'default'} label="封面">
              {wxCover === 'this' ? <span className="row"><img className="mini" src={imgUrl(draft.cover_wechat!)} alt="" /> 这篇选的封面，方形小图按你拖的位置裁</span>
                : wxCover === 'default' ? '没选，用设置里的默认封面' : '没有封面，先在“封面·配图”里选一张'}
            </Line>
            <Line ok label="正文图片">{pics ? `${pics} 张，推的时候自动传到公众号` : '没有'}</Line>
            <Line ok label="摘要">{draft.summary?.trim() || '没填，自动取正文开头'}</Line>
            <Line ok label="阅读原文">{draft.source_url || '不设'}</Line>
            <div className="callout info"><Ic.Alert size={15} /><span>只放进草稿箱，不会群发。之后在手机“公众号助手”或电脑后台再看一遍，点发表。重复推会在草稿箱里多出一篇。</span></div>
          </div>
        ) : (
          <div className="checks">
            <Line ok={!!status?.x_connected} label="X 账号">{status?.x_connected ? (xName || '已连接') : <a onClick={() => go('settings')}>还没连接，去设置</a>}</Line>
            {needsMedia && (
              <Line ok={!!status?.x_media} label="传图权限">{status?.x_media ? '有' : <a onClick={() => go('settings')}>这次要传图，需要在设置里点一次“重新连接”</a>}</Line>
            )}
            <Line ok={!!draft.title.trim()} label="标题">{draft.title.trim() || '长文必须有标题'}</Line>
            <Line ok={xCover !== 'none'} warn={xCover === 'wechat'} label="封面">
              {xCover === 'x' ? <span className="row"><img className="mini" src={imgUrl(draft.cover_x!)} alt="" /> X 长文封面</span>
                : xCover === 'wechat' ? '没单独选，用公众号封面' : '不带封面'}
            </Line>
            <Line ok label="正文图片">{pics ? `${pics} 张，推的时候自动传到 X` : '没有'}</Line>
            <div className="callout info"><Ic.Alert size={15} /><span>在 X 上建一篇长文草稿，不会公开发出。去 X 的长文草稿里看一遍再点发布。X 长文要求账号开通 Premium，接口按次计费，从开发者后台余额里扣。</span></div>
          </div>
        )}
        {holes.length > 0 && (
          <div className="callout"><Ic.Alert size={15} /><span>正文里还有 {holes.length} 处【】没补：{holes[0].slice(0, 40)}{holes.length > 1 ? ' 等' : ''}。推进草稿箱后也可以再改。</span></div>
        )}
        {err && <div className="err">{err}</div>}
      </div>
    </Dialog>
  )
}

/** Where this draft is archived in Feishu: one document, updated in place. */
export function FeishuLine({ draft, onChange }: { draft: Draft; onChange: () => void }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const state = draft.feishu_state
  const behind = !!draft.feishu_synced_version && draft.updated_at > draft.feishu_synced_version
  async function sync(force = false) {
    if (force && !window.confirm('飞书文档里的改动会被工作台的版本覆盖（飞书的历史版本里还能找回）。确定覆盖？')) return
    setBusy(true)
    try { await api.post(`/api/drafts/${draft.id}/feishu`, { force }); toast('已经排上，几十秒后飞书文档会更新'); window.setTimeout(onChange, 4000) }
    catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  return (
    <div className="feishu-line">
      <div className="row"><Ic.Feishu size={15} /> <b>飞书留档</b>
        {draft.feishu_url && <a href={draft.feishu_url} target="_blank" rel="noreferrer" className="small">打开文档 <Ic.External size={11} /></a>}
      </div>
      <div className="small muted">
        {state === 'syncing' ? '正在写入飞书…'
          : state === 'conflict' ? '飞书文档在工作台之外被改过，这次没有覆盖。先去飞书看看改了什么。'
          : state === 'failed' ? `上次同步没成功：${draft.feishu_error || ''}`
          : draft.feishu_doc ? `${behind ? '有新改动，停笔一分半钟后自动同步' : '已是最新'}${draft.feishu_synced_at ? `，上次同步 ${ago(draft.feishu_synced_at)}` : ''}。一篇稿子只对应这一个文档，改动记录在飞书的历史版本里。`
          : '还没建文档，停笔一分半钟后会自动在飞书“内容工作台 · 文章”文件夹里建一个。'}
      </div>
      <div className="row" style={{ gap: 6 }}>
        {state === 'conflict'
          ? <button className="btn sm" disabled={busy} onClick={() => sync(true)}>用工作台版本覆盖</button>
          : <button className="btn sm" disabled={busy || state === 'syncing'} onClick={() => sync(false)}>{busy ? <Ic.Loader size={13} /> : <Ic.Upload size={13} />} 现在同步</button>}
      </div>
    </div>
  )
}

/** Look for cases in 生财 MCP (深海圈 posts, 精华, project cases) while writing. */
export function CasesPanel({ draftId, onUse }: { draftId: number; onUse: (line: string) => void }) {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [list, setList] = useState<CaseSearch[] | null>(null)
  const load = () => api.get<CaseSearch[]>(`/api/drafts/${draftId}/cases`).then(setList).catch(() => {})
  usePoll(load, 5000, [draftId])
  async function search() {
    if (!query.trim()) return
    try { await api.post(`/api/drafts/${draftId}/cases`, { query }); setQuery(''); load() }
    catch (e) { toast((e as Error).message, 'error') }
  }
  return (
    <div className="stack cases">
      <div className="small muted">从生财 MCP 里找：深海圈帖子、精华区、项目案例库、航海资料。说清楚要什么样的案例，一两分钟出结果。圈友的案例发之前要确认能不能公开引用。</div>
      <div className="inline-form">
        <input className="input" value={query} onChange={e => setQuery(e.target.value)} placeholder="比如：新手做油管长视频三个月开通 YPP"
          onKeyDown={e => { if (e.key === 'Enter') search() }} />
        <button className="btn primary sm" onClick={search} disabled={!query.trim()}><Ic.Bulb size={14} /> 找</button>
      </div>
      {(list || []).map(s => (
        <div key={s.id} className="case-search">
          <div className="q"><b>{s.query}</b> <span className="faint small">{ago(s.created_at)}</span></div>
          {s.state === 'queued' && <div className="row small muted"><Ic.Loader size={13} /> 正在生财里翻…</div>}
          {s.state === 'failed' && <div className="err small">{s.error}</div>}
          {s.state === 'done' && s.results.length === 0 && <div className="small muted">没找到合适的。</div>}
          {s.results.map((c, i) => (
            <div key={i} className="case-item">
              <div className="t">{c.url ? <a href={c.url} target="_blank" rel="noreferrer">{c.title}</a> : c.title}
                {c.author && <span className="faint small"> · {c.author}</span>}
                {c.public && <span className="pill small">{c.public}</span>}</div>
              {c.summary && <div className="small">{c.summary}</div>}
              {c.fit && <div className="small muted">怎么用：{c.fit}</div>}
              <button className="btn ghost sm" onClick={() => { onUse(`案例：${c.title}${c.author ? `（${c.author}）` : ''} ${c.url || ''}`.trim()); toast('记到备注里了') }}>记到备注</button>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
