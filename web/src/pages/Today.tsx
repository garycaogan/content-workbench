import { useState } from 'react'
import { api, type Draft, type Idea, type Material, type Status } from '../api'
import { go } from '../App'
import { ago, greeting, PlatformIcon, SourceIcon, todayLabel, usePoll } from '../ui'
import { Composer } from './Composer'
import { IdeaCard } from './Ideas'
import * as Ic from '../icons'

export function Today({ status }: { status: Status | null }) {
  const [ideas, setIdeas] = useState<Idea[] | null>(null)
  const [drafts, setDrafts] = useState<Draft[] | null>(null)
  const [materials, setMaterials] = useState<Material[] | null>(null)

  const load = () => {
    api.get<Idea[]>('/api/ideas?status=new').then(setIdeas).catch(() => setIdeas([]))
    api.get<Draft[]>('/api/drafts').then(d => setDrafts(d.filter(x => x.status !== 'published'))).catch(() => setDrafts([]))
    api.get<Material[]>('/api/materials').then(setMaterials).catch(() => setMaterials([]))
  }
  usePoll(load, 7000)

  const working = (materials || []).filter(m => m.status === 'transcribing' || (status?.jobs_active && !m.idea_count && Date.now() / 1000 - m.created_at < 900))

  return (
    <div className="page">
      <div className="greet">
        <h1>{greeting()}{status?.author ? `，${status.author}` : ''}</h1>
        <p>{todayLabel()}</p>
      </div>

      <div className="pipeline">
        <button className="stat" onClick={() => go('ideas')}>
          <span className="ic"><Ic.Bulb size={18} /></span>
          <div><b>{status?.counts.ideas_new ?? '–'}</b><span>个选题等你挑</span></div>
        </button>
        <button className="stat" onClick={() => go('drafts')}>
          <span className="ic"><Ic.Doc size={18} /></span>
          <div><b>{status?.counts.drafts ?? '–'}</b><span>篇稿子在改</span></div>
        </button>
        <button className="stat" onClick={() => go('materials')}>
          <span className="ic"><Ic.Inbox size={18} /></span>
          <div><b>{status?.counts.materials ?? '–'}</b><span>条素材</span></div>
        </button>
      </div>

      <Composer status={status} onSaved={load} />

      {working.length > 0 && (
        <div className="card" style={{ marginTop: 14 }}>
          {working.map(m => (
            <button key={m.id} className="list-row" onClick={() => go(`materials/${m.id}`)}>
              <SourceIcon source={m.source} />
              <div className="grow"><div className="t">{m.title}</div><div className="sub">{m.status === 'transcribing' ? '正在本机转写' : '正在提炼选题'}</div></div>
              <Ic.Loader size={16} className="faint" />
            </button>
          ))}
        </div>
      )}

      <div className="two-col">
        <section>
          <h2 className="section-title"><Ic.Bulb size={15} /> 等你挑的选题
            {ideas && ideas.length > 4 && <a className="more" href="#/ideas">全部 {ideas.length} 个</a>}
          </h2>
          {ideas === null ? <div className="card card-pad"><div className="skeleton" style={{ height: 60 }} /></div> :
            ideas.length === 0 ? (
              <div className="card card-pad muted small">没有待挑的选题。记一笔或导入一段录音，几分钟后会出现在这里。</div>
            ) : (
              <div className="card">{ideas.slice(0, 4).map(i => <IdeaCard key={i.id} idea={i} onChange={load} compact />)}</div>
            )}
        </section>

        <section>
          <h2 className="section-title"><Ic.Doc size={15} /> 在改的稿子
            {drafts && drafts.length > 5 && <a className="more" href="#/drafts">全部</a>}
          </h2>
          {drafts === null ? <div className="card card-pad"><div className="skeleton" style={{ height: 60 }} /></div> :
            drafts.length === 0 ? (
              <div className="card card-pad muted small">还没有稿子。在左边挑一个选题，点“写成公众号”或“写成 X”。</div>
            ) : (
              <div className="card list-rows">
                {drafts.slice(0, 6).map(d => (
                  <button key={d.id} className="list-row" onClick={() => go(`drafts/${d.id}`)}>
                    <PlatformIcon platform={d.platform} />
                    <div className="grow">
                      <div className="t">{d.title || '（还没有标题）'}</div>
                      <div className="sub">{d.status === 'generating' ? '正在起草…' : d.status === 'failed' ? '起草失败' : d.preview || '空稿'}</div>
                    </div>
                    {d.status === 'generating' ? <Ic.Loader size={15} className="faint" /> : <span className="when">{ago(d.updated_at)}</span>}
                  </button>
                ))}
              </div>
            )}
        </section>
      </div>
    </div>
  )
}
