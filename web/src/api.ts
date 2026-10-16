export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const init: RequestInit = { method, credentials: 'same-origin', headers: {} }
  if (form) {
    init.body = form
  } else if (body !== undefined) {
    init.body = JSON.stringify(body)
    ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
  }
  const res = await fetch(path, init)
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
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
export type Draft = {
  id: number; idea_id: number | null; platform: 'wechat' | 'x'; title: string; body: string; notes: string
  preview?: string; status: string; error: string | null; created_at: number; updated_at: number
  publications?: Publication[]
  // long-form only
  images?: DraftImage[]; cover_wechat?: number | null; cover_x?: number | null
  cover_crop?: { square_x?: number }; summary?: string; source_url?: string
  // the one Feishu doc that archives this draft
  feishu_doc?: string | null; feishu_url?: string | null; feishu_state?: string | null
  feishu_error?: string | null; feishu_synced_at?: number | null; feishu_synced_version?: number | null
  last_revision?: string | null
  xhs?: XhsState
  video?: VideoState
}
export type VideoItem = { text?: string; sub?: string; cue?: string; accent?: string }
export type VideoRow = { label?: string; before?: string; after?: string; cue?: string }
export type VideoScene = {
  type: 'hook' | 'points' | 'shot' | 'stat' | 'change' | 'quote' | 'cta'; say: string
  eyebrow?: string; title?: string; accent?: string; title2?: string; accent2?: string
  lead?: string; sub?: string; words?: string[]; chips?: VideoItem[]; box?: string
  items?: VideoItem[]; footer?: string; footer_accent?: string
  image?: number; focus?: number[]; label?: string; badges?: VideoItem[]
  value?: string; unit?: string; then?: string; then_unit?: string
  rows?: VideoRow[]; lines?: string[]; lines_cue?: string[]
  badge?: string; command?: string; sign?: string; sign_sub?: string
  [cue: `${string}_cue`]: string | string[] | undefined
}
export type VideoState = {
  scenes?: VideoScene[]; voice?: string; bgm?: boolean; status?: '' | 'planning' | 'rendering'; stage?: string; pct?: number
  error?: string | null; planned_at?: number; edited_at?: number
  video?: { duration: number; size: number; rendered_at: number; scenes: { start: number; duration: number }[] }
}
/** One long-form draft on the 视频 page. */
export type VideoOverview = {
  id: number; title: string; draft_status: string; updated_at: number; has_body: boolean
  status: '' | 'planning' | 'rendering'; stage: string; pct: number; error: string | null
  scenes: number; seconds: number; planned_at?: number; edited_at?: number; video: VideoState['video'] | null
}
/** A scene built for the browser to play (see video.preview on the server). */
export type VideoPreview = { url: string; audio: string | null; duration: number; voiced: boolean }
export type VideoOptions = { voices: { id: string; name: string; desc: string }[]; default_voice: string; tts: boolean
  whisper: boolean; bgm: boolean; types: Record<string, string> }
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
