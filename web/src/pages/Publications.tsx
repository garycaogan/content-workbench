import { useEffect, useState } from 'react'
import { api, PUB_WHERE, type Publication } from '../api'
import { go } from '../App'
import { ago, Empty, PlatformIcon, useToast } from '../ui'
import * as Ic from '../icons'

export function Publications() {
  const toast = useToast()
  const [items, setItems] = useState<Publication[] | null>(null)
  useEffect(() => { api.get<Publication[]>('/api/publications').then(setItems).catch(e => toast(e.message, 'error')) }, [toast])

  return (
    <div className="page narrow">
      <div className="page-head">
        <div>
          <h1>已发布</h1>
          <p>每一次发到 X、推进公众号和星球草稿箱、推送视频的记录。结果不确定的，去平台上确认一下再决定要不要重发。</p>
        </div>
      </div>
      {items === null ? (
        <div className="card stack card-pad">{[0, 1].map(i => <div key={i} className="skeleton" style={{ height: 42 }} />)}</div>
      ) : items.length === 0 ? (
        <div className="card"><Empty icon={<Ic.Send />} title="还没发过东西">在稿子编辑页右上角点“发到 X”或“推到草稿箱”，视频在视频工作台的“发布”里推。</Empty></div>
      ) : (
        <div className="card list-rows">
          {items.map(p => {
            const ok = p.state === 'sent'
            return (
              <button key={p.id} className="list-row" onClick={() => p.draft_id && go(`drafts/${p.draft_id}`)}>
                <PlatformIcon platform={p.platform} />
                <div className="grow">
                  <div className="t">{p.title || '（稿子已删除）'}</div>
                  <div className="sub">{PUB_WHERE[p.platform] || p.platform}</div>
                </div>
                <span className={'pill ' + (ok ? 'ok' : p.state === 'unknown' || p.state === 'pending' ? 'warn' : 'danger')}>
                  {ok ? (p.platform === 'x' || p.platform === 'x_video' ? '已发出' : p.platform === 'wechat_video' ? '已推送' : '已进草稿箱')
                    : p.state === 'pending' ? '发送中' : p.state === 'unknown' ? '结果不确定' : '失败'}
                </span>
                {(p.platform === 'x' || p.platform === 'x_video') && p.remote_id && (
                  <a className="btn ghost icon sm" aria-label="在 X 上查看" href={`https://x.com/i/status/${p.remote_id.split(',')[0]}`} target="_blank" rel="noreferrer"
                    onClick={e => e.stopPropagation()}><Ic.External size={14} /></a>
                )}
                <span className="when">{ago(p.created_at)}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
