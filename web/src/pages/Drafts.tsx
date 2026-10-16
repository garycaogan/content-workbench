import { useState } from 'react'
import { api, type Draft } from '../api'
import { go } from '../App'
import { ago, Empty, PlatformIcon, usePoll, useToast } from '../ui'
import * as Ic from '../icons'

export function Drafts() {
  const toast = useToast()
  const [drafts, setDrafts] = useState<Draft[] | null>(null)
  const [filter, setFilter] = useState<'open' | 'published' | 'all'>('open')
  const load = () => api.get<Draft[]>('/api/drafts').then(setDrafts).catch(e => toast(e.message, 'error'))
  usePoll(load, 6000)

  async function blank(platform: 'wechat' | 'x') {
    try {
      const r = await api.post<{ id: number }>('/api/drafts', { platform, title: '', body: '' })
      go(`drafts/${r.id}`)
    } catch (e) { toast((e as Error).message, 'error') }
  }

  const shown = (drafts || []).filter(d => filter === 'all' || (filter === 'published' ? d.status === 'published' : d.status !== 'published'))

  return (
    <div className="page narrow">
      <div className="page-head">
        <div>
          <h1>稿件</h1>
          <p>AI 起的底稿和你自己写的稿子都在这里，改好了在编辑页里一键发出。</p>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => blank('wechat')}><Ic.Doc size={14} /> 新长文</button>
          <button className="btn" onClick={() => blank('x')}><Ic.XLogo size={12} /> 新 X 推文</button>
          <button className="btn mobile-only" onClick={() => go('videos')}><Ic.Film size={14} /> 视频</button>
        </div>
      </div>

      <div className="row" style={{ marginBottom: 14 }}>
        <div className="segmented" role="group" aria-label="筛选">
          {([['open', '在改'], ['published', '已发出'], ['all', '全部']] as const).map(([k, l]) => (
            <button key={k} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l}</button>
          ))}
        </div>
      </div>

      {drafts === null ? (
        <div className="card stack card-pad">{[0, 1, 2].map(i => <div key={i} className="skeleton" style={{ height: 42 }} />)}</div>
      ) : shown.length === 0 ? (
        <div className="card"><Empty icon={<Ic.Doc />} title="没有稿子">在“选题”里挑一个，点“写成公众号”或“写成 X”，或者在右上角新建一篇空稿。</Empty></div>
      ) : (
        <div className="card list-rows">
          {shown.map(d => (
            <button key={d.id} className="list-row" onClick={() => go(`drafts/${d.id}`)}>
              <PlatformIcon platform={d.platform} />
              <div className="grow">
                <div className="t">{d.title || '（还没有标题）'}</div>
                <div className="sub">{d.status === 'generating' ? '正在按你的文风起草…' : d.status === 'failed' ? `起草失败：${d.error || ''}` : d.preview || '空稿'}</div>
              </div>
              {d.status === 'generating' && <span className="pill accent"><Ic.Loader size={12} /> 起草中</span>}
              {d.status === 'revising' && <span className="pill accent"><Ic.Loader size={12} /> AI 按批注改稿中</span>}
              {d.status === 'failed' && <span className="pill danger">失败</span>}
              {d.status === 'published' && <span className="pill ok"><Ic.Check size={12} /> 已发出</span>}
              <span className="when">{ago(d.updated_at)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
