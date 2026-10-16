import { useState } from 'react'
import { api, type VideoOverview } from '../api'
import { go } from '../App'
import { ago, Empty, usePoll, useToast } from '../ui'
import { fmtDur } from './Video'
import * as Ic from '../icons'

const started = (r: VideoOverview) => r.scenes > 0 || !!r.status || !!r.video

function where(r: VideoOverview): { text: string; tone?: 'accent' | 'ok' | 'warn' | 'danger' } {
  if (r.status === 'planning') return { text: '正在写脚本', tone: 'accent' }
  if (r.status === 'rendering') return { text: `出片中 ${r.pct || 0}%`, tone: 'accent' }
  if (r.error) return { text: '上次失败了', tone: 'danger' }
  if (r.video && r.edited_at && r.edited_at > r.video.rendered_at) return { text: '改过，还没重新出片', tone: 'warn' }
  if (r.video) return { text: '已出片', tone: 'ok' }
  return { text: '还没出片' }
}

/** Every long-form draft's video: the ones under way first, then drafts that could become one. */
export function Videos() {
  const toast = useToast()
  const [rows, setRows] = useState<VideoOverview[] | null>(null)
  usePoll(() => { api.get<VideoOverview[]>('/api/videos').then(setRows).catch(e => toast((e as Error).message, 'error')) }, 5000)

  const made = (rows || []).filter(started)
  const ready = (rows || []).filter(r => !started(r) && r.has_body).slice(0, 12)

  return (
    <div className="page narrow">
      <div className="page-head">
        <div>
          <h1>视频</h1>
          <p>把长文做成竖屏口播视频。AI 先写分镜，你一个镜头一个镜头地改、当场预览，满意了再出片。</p>
        </div>
      </div>

      {rows === null ? (
        <div className="card stack card-pad">{[0, 1, 2].map(i => <div key={i} className="skeleton" style={{ height: 52 }} />)}</div>
      ) : !made.length && !ready.length ? (
        <div className="card"><Empty icon={<Ic.Film />} title="还没有能做成视频的长文">先在“稿件”里写一篇长文，写好了这里就能做成视频。</Empty></div>
      ) : null}

      {made.length > 0 && (
        <section className="vlist">
          <h2 className="section-title">做过的</h2>
          <div className="card list-rows">
            {made.map(r => {
              const w = where(r)
              return (
                <button key={r.id} className="list-row" onClick={() => go(`videos/${r.id}`)}>
                  <span className="vlist-thumb">
                    {r.video ? <video src={`/api/drafts/${r.id}/video.mp4?v=${Math.round(r.video.rendered_at)}#t=1`} muted playsInline preload="metadata" />
                      : <Ic.Film size={16} />}
                  </span>
                  <div className="grow">
                    <div className="t">{r.title || '（还没有标题）'}</div>
                    <div className="sub">
                      {r.status === 'rendering' ? r.stage || '排队中'
                        : r.error && !r.status ? r.error
                          : `${r.scenes} 个镜头 · ${r.video ? fmtDur(r.video.duration) : `约 ${fmtDur(r.seconds)}`}`
                            + (r.video ? ` · ${ago(r.video.rendered_at)} 出片` : '')}
                    </div>
                  </div>
                  <span className={'pill' + (w.tone ? ` ${w.tone}` : '')}>
                    {(r.status === 'planning' || r.status === 'rendering') && <Ic.Loader size={12} />} {w.text}
                  </span>
                </button>
              )
            })}
          </div>
        </section>
      )}

      {ready.length > 0 && (
        <section className="vlist">
          <h2 className="section-title">可以做成视频的长文</h2>
          <div className="card list-rows">
            {ready.map(r => (
              <button key={r.id} className="list-row" onClick={() => go(`videos/${r.id}`)}>
                <span className="vlist-thumb"><Ic.Doc size={16} /></span>
                <div className="grow">
                  <div className="t">{r.title || '（还没有标题）'}</div>
                  <div className="sub">{r.draft_status === 'published' ? '已发出' : '在改'} · {ago(r.updated_at)} 改过</div>
                </div>
                <span className="btn sm">做成视频 <Ic.Right size={13} /></span>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
