export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown, form?: FormData | Blob): Promise<T> {
  const init: RequestInit = { method, credentials: 'same-origin', headers: {} }
  if (form) {
    init.body = form
  } else if (body !== undefined) {
    init.body = JSON.stringify(body)
    ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
  }
  const res = await fetch(path, init)
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    // Not our JSON: the Cloudflare gateway answers with its own HTML page, e.g. 524 when a request runs past ~100 s
    const slow = [502, 504, 524].includes(res.status) || res.ok
    throw new ApiError(res.status, slow
      ? `等太久，被网关先断开了（${res.status}）。服务器可能还在后台处理，过一两分钟刷新，看推送记录或结果再决定要不要重试。`
      : `服务器返回了看不懂的内容（${res.status}），刷新页面再试一次。`)
  }
  if (!res.ok) {
    const detail = data && typeof data.detail === 'string' ? data.detail : `请求失败（${res.status}）`
    if (res.status === 401) window.dispatchEvent(new Event('cw-logged-out'))
    throw new ApiError(res.status, detail)
  }
  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  put: <T>(path: string, body: unknown) => request<T>('PUT', path, body),
  patch: <T>(path: string, body: unknown) => request<T>('PATCH', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, form: FormData) => request<T>('POST', path, undefined, form),
  /** Raw bytes as the request body (a piece of a video upload). */
  putBytes: <T>(path: string, data: Blob) => request<T>('PUT', path, undefined, data),
}

export type Material = {
  id: number; source: string; source_ref: string; title: string; preview?: string; body?: string
  length?: number; status: string; error: string | null; created_at: number; idea_count?: number
}
export type Idea = {
  id: number; material_id: number | null; material_title?: string; title: string; angle: string
  platforms: string[]; quotes: string[]; notes: string; starred: number; status: string; created_at: number
}
export type DraftImage = { id: number; name: string; mime: string; width: number; height: number; bytes: number; created_at: number }
/** A video in a long-form body: uploaded in pieces, then made into an MP4 that X and browsers play. */
export type DraftClip = {
  id: number; draft_id: number; name: string; state: 'uploading' | 'processing' | 'ready' | 'failed'; error: string | null
  width: number | null; height: number | null; duration: number | null; bytes: number | null; size: number; received: number
  link: string                      // where readers can watch it; 星球 articles can't hold a video
  created_at: number
}
/** The fields the editor saves; each one carries a fingerprint (rev) of the version the page loaded. */
export const DRAFT_FIELDS = ['title', 'body', 'notes', 'summary', 'source_url', 'context'] as const
export type DraftField = typeof DRAFT_FIELDS[number]
export type DraftRevs = Record<DraftField, string>
export type Draft = {
  id: number; idea_id: number | null; platform: 'wechat' | 'x'; title: string; body: string; notes: string
  preview?: string; status: string; error: string | null; created_at: number; updated_at: number
  rev?: DraftRevs
  publications?: Publication[]
  // long-form only
  images?: DraftImage[]; clips?: DraftClip[]; cover_wechat?: number | null; cover_x?: number | null
  cover_crop?: { square_x?: number }; summary?: string; source_url?: string
  // the one Feishu doc that archives this draft
  feishu_doc?: string | null; feishu_url?: string | null; feishu_state?: string | null
  feishu_error?: string | null; feishu_synced_at?: number | null; feishu_synced_version?: number | null
  last_revision?: string | null
  context?: string                  // 背景资料: facts for the AI, never published or archived
  xhs?: XhsState
  video?: VideoState
}
export type VideoItem = { text?: string; sub?: string; cue?: string; accent?: string; icon?: string }
export type VideoRow = { label?: string; before?: string; after?: string; cue?: string }
export type VideoScene = {
  type: 'hook' | 'points' | 'shot' | 'clip' | 'stat' | 'change' | 'quote' | 'cta'; say: string
  eyebrow?: string; title?: string; accent?: string; title2?: string; accent2?: string
  lead?: string; sub?: string; words?: string[]; chips?: VideoItem[]; box?: string
  items?: VideoItem[]; footer?: string; footer_accent?: string; keys?: string[]
  image?: number; focus?: number[]; label?: string; badges?: VideoItem[]
  value?: string; unit?: string; then?: string; then_unit?: string
  rows?: VideoRow[]; lines?: string[]; lines_cue?: string[]
  badge?: string; command?: string; sign?: string; sign_sub?: string
  /** 原片片段: a body video, played from clip_start to clip_end (its end when left out) */
  clip?: number; clip_start?: number; clip_end?: number; fill?: 'full' | 'window'; sound?: boolean; source?: string
  [cue: `${string}_cue`]: string | string[] | undefined
}
export type VideoFormat = 'vertical' | 'landscape'
export type VideoRender = { duration: number; size: number; rendered_at: number; style?: string; scenes: { start: number; duration: number }[] }
export type VideoState = {
  scenes?: VideoScene[]; voice?: string; bgm?: boolean; style?: string; headline?: string[]; headline_key?: string
  status?: '' | 'planning' | 'rendering'; stage?: string; pct?: number
  rendering?: VideoFormat | null; error?: string | null; planned_at?: number; edited_at?: number
  video?: VideoRender; video_landscape?: VideoRender
  meta?: VideoMeta; meta_status?: '' | 'generating'; meta_error?: string | null
}
/** What goes out with the render: title and intro for the post, words for the cover. */
export type VideoCover = { lines: string[]; key: string; kicker: string; image: number | null }
export type VideoMeta = { title: string; intro: string; cover: VideoCover; generated_at?: number | null; edited_at?: number }
/** The last render of one format ("video" holds the vertical one). */
export const renderOf = (s: VideoState, f: VideoFormat) => (f === 'vertical' ? s.video : s.video_landscape)
/** One long-form draft on the 视频 page. */
export type VideoOverview = {
  id: number; title: string; draft_status: string; updated_at: number; has_body: boolean
  status: '' | 'planning' | 'rendering'; stage: string; pct: number; error: string | null
  scenes: number; seconds: number; planned_at?: number; edited_at?: number; style: string
  video: VideoRender | null; video_landscape: VideoRender | null
}
/** A scene built for the browser to play (see video.preview on the server). */
export type VideoPreview = { url: string; audio: string | null; duration: number; voiced: boolean }
export type VideoOptions = { voices: { id: string; name: string; desc: string }[]; default_voice: string; tts: boolean
  whisper: boolean; bgm: boolean; types: Record<string, string>
  styles: { id: string; name: string; hint: string; headline: boolean }[]; formats: { id: VideoFormat; name: string }[] }
export type XhsCover = { kicker?: string; lines?: string[]; highlight?: string; subtitle?: string; pills?: string[] }
export type XhsCard = { title?: string; points?: string[] }
export type XhsState = { title?: string; body?: string; tags?: string[]; cover?: XhsCover; cards?: XhsCard[]
  card_images?: number[]; extra_images?: number[]; generating?: boolean; error?: string | null; generated_at?: number }
export type CommentMsg = { id: number; author: 'gary' | 'ai'; body: string; created_at: number }
export type Thread = CommentMsg & { draft_id: number; quote: string | null; prefix: string | null; suffix: string | null
  status: 'open' | 'resolved'; replies: CommentMsg[] }
export type RevisionInfo = { before: number; after: number; summary: string; at: number; threads: number; answered: number }
export type DiffBlock = { op: 'same' | 'del' | 'ins'; text: string } | { op: 'change'; parts: { op: 'same' | 'del' | 'ins'; text: string }[] }
export type CaseItem = { title: string; author?: string; url?: string; summary?: string; fit?: string; public?: string }
export type CaseSearch = { id: number; query: string; state: string; results: CaseItem[]; error: string | null; created_at: number }
export type Publication = {
  id: number; draft_id: number; platform: string; state: string; remote_id: string | null
  detail: string; created_at: number; title?: string
}
/** A render on its way to X or the 公众号 素材库; stage and pct only while it runs. */
export type VideoSend = Publication & { stage?: string; pct?: number }

/** Where each kind of publication went, as the author reads it. */
export const PUB_WHERE: Record<string, string> = {
  x: 'X', x_article: 'X 长文草稿', zsxq: '星球草稿箱', wechat: '公众号草稿箱', x_video: 'X 视频', wechat_video: '公众号素材库',
}

export function pubLabel(p: Publication): string {
  const where = PUB_WHERE[p.platform] || p.platform
  if (p.state === 'pending') return `${where}：发送中`
  if (p.state === 'unknown') return `${where}：结果不确定，去平台上看一眼`
  if (p.state !== 'sent') return `${where}：失败`
  return p.platform === 'x' ? '已发出' : p.platform === 'x_video' ? '视频已发到 X' : p.platform === 'wechat_video' ? '视频已进公众号素材库' : `已进${where}`
}

/** X counts CJK and emoji double and every link as 23 (twitter-text v3). */
export function xWeighted(text: string): number {
  let n = 0
  for (const part of text.split(/(https?:\/\/\S+)/)) {
    if (/^https?:\/\//.test(part)) { n += 23; continue }
    for (const ch of part) {
      const c = ch.codePointAt(0) || 0
      n += c <= 0x10ff || (c >= 0x2000 && c <= 0x200d) || (c >= 0x2010 && c <= 0x201f) || (c >= 0x2032 && c <= 0x2037) ? 1 : 2
    }
  }
  return n
}
export const X_LIMIT = 280
export type Status = {
  llm: { ok: boolean; detail: string }; transcription: boolean; x_connected: boolean; x_media: boolean; zsxq_connected: boolean
  zsxq_group?: string; case_search?: boolean; author?: string
  wechat_configured: boolean; wechat_cover: boolean
  counts: { materials: number; ideas_new: number; drafts: number }; jobs_active: number
}

export const SOURCE_LABEL: Record<string, string> = {
  note: '随手记', audio: '录音', feishu_minutes: '飞书妙记', feishu_doc: '飞书文档', feishu_table: '飞书表格', paste: '粘贴',
}
export const PLATFORM_LABEL: Record<string, string> = { wechat: '长文', x: 'X' }

export function when(ts: number): string {
  const d = new Date(ts * 1000)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = d.toTimeString().slice(0, 5)
  if (sameDay) return `今天 ${hm}`
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`
}
