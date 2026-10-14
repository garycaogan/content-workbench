import { useEffect, useRef, useState } from 'react'
import { startRegistration } from '@simplewebauthn/browser'
import { api, type Status } from '../api'
import { deviceLabel } from '../Login'
import { ago, Dialog, useToast } from '../ui'
import * as Ic from '../icons'

type SettingsData = {
  author: string; rp_id: string; origins: string[]; x_redirect_uri: string
  secrets: Record<string, boolean>; wechat_cover: boolean; login_mode?: 'access' | 'passkey'; access_emails?: string[]
  x_account: { username?: string; name?: string; subscription_type?: string } | null
}
type Cred = { id: string; label: string; rp_id: string; created_at: number; last_used_at: number | null }

export function SettingsPage({ query, status }: { query: URLSearchParams; status: Status | null }) {
  const toast = useToast()
  const [s, setS] = useState<SettingsData | null>(null)
  const [creds, setCreds] = useState<Cred[]>([])
  const [wxSecret, setWxSecret] = useState('')
  const [author, setAuthor] = useState<string | null>(null)
  const [xKeys, setXKeys] = useState({ x_client_id: '', x_client_secret: '' })
  const [busy, setBusy] = useState('')
  const coverRef = useRef<HTMLInputElement>(null)
  const [zsxq, setZsxq] = useState<{ connected: boolean; account: { name?: string } | null; group: { id: string; name: string } } | null>(null)
  const [zsxqOpen, setZsxqOpen] = useState(false)
  const loadZsxq = () => api.get<typeof zsxq>('/api/zsxq').then(setZsxq).catch(() => {})
  useEffect(() => { loadZsxq() }, [])

  const load = () => {
    api.get<SettingsData>('/api/settings').then(setS).catch(e => toast(e.message, 'error'))
    api.get<Cred[]>('/api/auth/credentials').then(setCreds).catch(() => {})
    window.dispatchEvent(new Event('cw-refresh-status'))
  }
  useEffect(load, [])
  useEffect(() => {
    const x = query.get('x')
    if (x === 'ok') toast('X 已连接')
    if (x === 'failed') toast('X 连接失败，再点一次连接', 'error')
    if (x === 'denied') toast('你在 X 上取消了授权', 'error')
  }, [query, toast])

  async function saveSecrets(body: Record<string, string>, msg: string) {
    const clean = Object.fromEntries(Object.entries(body).filter(([, v]) => v.trim()))
    if (!Object.keys(clean).length) return
    try { await api.put('/api/settings/secrets', clean); toast(msg); load() } catch (e) { toast((e as Error).message, 'error') }
  }
  async function connectX() {
    setBusy('x')
    try { const r = await api.get<{ url: string }>('/api/publish/x/connect'); window.location.href = r.url }
    catch (e) { toast((e as Error).message, 'error'); setBusy('') }
  }
  async function uploadCover(file: File) {
    setBusy('cover')
    const f = new FormData(); f.append('file', file)
    try { await api.upload('/api/settings/wechat-cover', f); toast('默认封面已传到公众号素材库'); load() }
    catch (e) { toast((e as Error).message, 'error') } finally { setBusy('') }
  }
  async function addDevice() {
    try {
      const o = await api.post<{ challenge_id: string; options: string }>('/api/auth/register/options', {})
      const credential = await startRegistration({ optionsJSON: JSON.parse(o.options) })
      await api.post('/api/auth/register/verify', { challenge_id: o.challenge_id, credential, label: deviceLabel() })
      toast('这台设备可以用 passkey 登录了'); load()
    } catch (e) { toast((e as Error).message, 'error') }
  }
  async function logout() {
    await api.post('/api/auth/logout').catch(() => {})
    // with email-code login the Cloudflare Access session is the login; end it too
    if (s?.login_mode === 'access') window.location.href = '/cdn-cgi/access/logout'
    else window.location.reload()
  }

  if (!s) return <div className="page narrow"><div className="skeleton" style={{ height: 30, width: '30%' }} /></div>
  const wxReady = s.secrets.wx_appid && s.secrets.wx_secret

  return (
    <div className="page narrow">
      <div className="page-head"><div><h1>设置</h1><p>发布渠道、本机能力和登录设备。所有凭证只存在这台电脑的私有目录里。</p></div></div>

      <section className="settings-group">
        <h2>发布渠道</h2>
        <div className="card">
          <div className="setting">
            <span className="ic x"><Ic.XLogo size={16} /></span>
            <div className="body">
              <div className="t">X</div>
              <div className="d">
                {s.x_account ? <>已连接 <b>@{s.x_account.username}</b>{s.x_account.subscription_type ? `（${s.x_account.subscription_type}）` : ''}，发帖和长文草稿按次计费，从开发者后台的余额里扣。</> : '还没连接。连接后可以从编辑页直接发推，或者把长文推进 X 的长文草稿。'}
                {s.x_account && !status?.x_media && <div className="callout" style={{ marginTop: 10 }}><Ic.Alert size={15} /><span>长文里的图片和封面要传到 X，需要多一项上传图片的权限。点右边“重新连接”，在 X 上同意一次就好。</span></div>}
              </div>
              <details className="adv">
                <summary><Ic.Right size={12} /> 开发者应用</summary>
                <div className="small muted" style={{ margin: '8px 0' }}>回调地址：<code>{s.x_redirect_uri}</code>。Client ID {s.secrets.x_client_id ? '已填' : '未填'}{s.secrets.x_client_secret ? '，Client Secret 已填' : '，公开客户端不需要 Secret'}。</div>
                <div className="inline-form">
                  <input className="input" placeholder="换一个 Client ID" value={xKeys.x_client_id} onChange={e => setXKeys({ ...xKeys, x_client_id: e.target.value })} />
                  <input className="input" type="password" autoComplete="off" placeholder="Client Secret（可选）" value={xKeys.x_client_secret} onChange={e => setXKeys({ ...xKeys, x_client_secret: e.target.value })} />
                  <button className="btn" onClick={() => { saveSecrets(xKeys, '已保存'); setXKeys({ x_client_id: '', x_client_secret: '' }) }}>保存</button>
                </div>
              </details>
            </div>
            <div className="side-actions">
              {s.x_account ? (
                <>
                  <span className="pill ok"><span className="dot on" /> 已连接</span>
                  <button className="btn sm" onClick={connectX} disabled={!!busy}>重新连接</button>
                  <button className="btn ghost sm" onClick={async () => { await api.post('/api/settings/x/disconnect'); toast('已断开'); load() }}>断开</button>
                </>
              ) : (
                <button className="btn primary" disabled={!s.secrets.x_client_id || !!busy} onClick={connectX}>{busy === 'x' ? <Ic.Loader size={15} /> : <Ic.XLogo size={13} />} 连接 X 账号</button>
              )}
            </div>
          </div>

          <div className="setting">
            <span className="ic wechat"><Ic.WechatLogo size={18} /></span>
            <div className="body">
              <div className="t">公众号</div>
              <div className="d">个人主体、未认证的号只能推进草稿箱，发表要你在手机上点。AppID {s.secrets.wx_appid ? '已填' : '未填'}，AppSecret {s.secrets.wx_secret ? '已填' : '未填'}，记得把这台电脑的出口 IP 加进公众号后台的 IP 白名单。</div>
              {!s.secrets.wx_secret && (
                <div className="inline-form">
                  <input className="input" type="password" autoComplete="off" placeholder="粘贴开发者密码 AppSecret" value={wxSecret} onChange={e => setWxSecret(e.target.value)} />
                  <button className="btn primary" disabled={!wxSecret.trim()} onClick={() => { saveSecrets({ wx_secret: wxSecret }, 'AppSecret 已保存'); setWxSecret('') }}>保存</button>
                </div>
              )}
              {s.secrets.wx_secret && (
                <details className="adv">
                  <summary><Ic.Right size={12} /> 换 AppSecret</summary>
                  <div className="inline-form">
                    <input className="input" type="password" autoComplete="off" placeholder="新的 AppSecret" value={wxSecret} onChange={e => setWxSecret(e.target.value)} />
                    <button className="btn" disabled={!wxSecret.trim()} onClick={() => { saveSecrets({ wx_secret: wxSecret }, 'AppSecret 已更新'); setWxSecret('') }}>保存</button>
                  </div>
                </details>
              )}
            </div>
            <div className="side-actions">
              {wxReady ? <span className="pill ok"><span className="dot on" /> 可以推送</span> : <span className="pill warn"><span className="dot off" /> 待配置</span>}
            </div>
          </div>

          <div className="setting">
            <span className="ic zsxq">星</span>
            <div className="body">
              <div className="t">知识星球</div>
              <div className="d">{zsxq?.connected
                ? <>已连接{zsxq.account?.name ? <> <b>{zsxq.account.name}</b></> : ''}，长文可以推进{zsxq.group.name ? `「${zsxq.group.name}」` : '你填的'}星球的「我的文章-草稿箱」，发布在星球里点。</>
                : <>还没连接。连接后长文可以推进星球的文章草稿箱。用微信扫一次码，登录过期了再扫。</>}</div>
              <ZsxqGroup group={zsxq?.group} onSaved={loadZsxq} />
            </div>
            <div className="side-actions">
              {zsxq?.connected ? <>
                <span className="pill ok"><span className="dot on" /> 已连接</span>
                <button className="btn sm" onClick={() => setZsxqOpen(true)}>重新扫码</button>
                <button className="btn ghost sm" onClick={async () => { await api.post('/api/zsxq/disconnect'); toast('已断开'); loadZsxq() }}>断开</button>
              </> : <button className="btn primary" onClick={() => setZsxqOpen(true)}>扫码连接</button>}
            </div>
          </div>

          <div className="setting">
            <span className="ic"><Ic.Image size={17} /></span>
            <div className="body">
              <div className="t">公众号默认封面</div>
              <div className="d">{s.wechat_cover ? '已上传。每篇长文可以在编辑页单独选封面，没选的时候用这张。' : '每篇长文可以在编辑页单独选封面；这里传的是没选时兜底用的那张，jpg 或 png，建议 900×383。'}</div>
            </div>
            <div className="side-actions">
              <button className="btn sm" disabled={!wxReady || !!busy} onClick={() => coverRef.current?.click()}>
                {busy === 'cover' ? <Ic.Loader size={14} /> : <Ic.Upload size={14} />} {s.wechat_cover ? '换一张' : '上传封面'}
              </button>
              <input ref={coverRef} type="file" accept="image/jpeg,image/png" hidden onChange={e => { const f = e.target.files?.[0]; if (f) uploadCover(f); e.target.value = '' }} />
            </div>
          </div>

          <div className="setting">
            <span className="ic"><Ic.Pen size={17} /></span>
            <div className="body">
              <div className="t">署名</div>
              <div className="inline-form" style={{ marginTop: 8 }}>
                <input className="input" value={author ?? s.author} onChange={e => setAuthor(e.target.value)} />
                <button className="btn" disabled={author === null || author === s.author}
                  onClick={async () => { await api.put('/api/settings', { author }); setAuthor(null); toast('署名已保存'); load() }}>保存</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <ZsxqLogin open={zsxqOpen} onClose={() => setZsxqOpen(false)} onDone={() => { setZsxqOpen(false); loadZsxq(); window.dispatchEvent(new Event('cw-refresh-status')) }} />

      <section className="settings-group">
        <h2>本机能力</h2>
        <div className="card">
          <div className="setting">
            <span className="ic"><Ic.Sparkle size={17} /></span>
            <div className="body">
              <div className="t">提炼和起草</div>
              <div className="d">用的是这台电脑上登录的 Claude Code（你自己的订阅），文字会发给 Anthropic 处理。</div>
            </div>
            <div className="side-actions">{status?.llm.ok ? <span className="pill ok"><span className="dot on" /> 可用</span> : <span className="pill warn">{status?.llm.detail || '检查中'}</span>}</div>
          </div>
          <div className="setting">
            <span className="ic"><Ic.Mic size={17} /></span>
            <div className="body">
              <div className="t">录音转写</div>
              <div className="d">在本机用 Whisper 转写，音频不出这台电脑。</div>
            </div>
            <div className="side-actions">{status?.transcription ? <span className="pill ok"><span className="dot on" /> 可用</span> : <span className="pill warn">没装好</span>}</div>
          </div>
        </div>
      </section>

      <section className="settings-group">
        <h2>登录与设备</h2>
        {s.login_mode === 'access' ? (
          <div className="card">
            <div className="setting">
              <span className="ic"><Ic.Key size={16} /></span>
              <div className="body">
                <div className="t">邮箱验证码登录</div>
                <div className="d">在任何设备打开 {s.origins[0].replace('https://', '')}，收 {(s.access_emails || []).join('、') || '你的邮箱'} 的验证码就能进，每台设备一个月验证一次，不用再绑 passkey。</div>
              </div>
            </div>
            <div className="setting">
              <span className="ic"><Ic.Logout size={16} /></span>
              <div className="body"><div className="t">退出登录</div><div className="d">只退出这台设备，下次进来要重新收验证码。</div></div>
              <div className="side-actions"><button className="btn ghost sm danger" onClick={logout}>退出</button></div>
            </div>
          </div>
        ) : (
        <div className="card">
          {creds.map(c => (
            <div key={c.id} className="setting">
              <span className="ic"><Ic.Key size={16} /></span>
              <div className="body">
                <div className="t">{c.label === 'MacIntel' ? 'Mac' : c.label || '设备'}</div>
                <div className="d">添加于 {ago(c.created_at)}{c.last_used_at ? `，最近使用 ${ago(c.last_used_at)}` : ''}</div>
              </div>
            </div>
          ))}
          <div className="setting">
            <span className="ic"><Ic.Plus size={16} /></span>
            <div className="body">
              <div className="t">在别的设备上用</div>
              <div className="d">手机或另一台电脑打开 {s.origins[0].replace('https://', '')}，先收邮箱验证码，再用 passkey 登录。如果那台设备上没有 passkey，就在这里点“给当前设备加 passkey”，或者用 iCloud 钥匙串同步过去。</div>
            </div>
            <div className="side-actions">
              <button className="btn sm" onClick={addDevice}><Ic.Key size={14} /> 给当前设备加 passkey</button>
            </div>
          </div>
          <div className="setting">
            <span className="ic"><Ic.Logout size={16} /></span>
            <div className="body"><div className="t">退出登录</div><div className="d">只退出这台设备。</div></div>
            <div className="side-actions"><button className="btn ghost sm danger" onClick={logout}>退出</button></div>
          </div>
        </div>
        )}
      </section>
    </div>
  )
}

type LoginState = { state: 'idle' | 'starting' | 'waiting' | 'ok' | 'expired' | 'failed'; has_qr: boolean; error?: string; name?: string; at?: number }

/** Scan the 知识星球 web login QR (shown from a headless browser on this machine) with WeChat. */
function ZsxqLogin({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const toast = useToast()
  const [agree, setAgree] = useState(false)
  const [st, setSt] = useState<LoginState | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => { if (open) { setSt(null); setAgree(false) } }, [open])
  useEffect(() => {
    if (!open || !st || !['starting', 'waiting'].includes(st.state)) return
    const t = window.setTimeout(async () => {
      try {
        const s = await api.get<LoginState>('/api/zsxq/login')
        setSt(s); setTick(n => n + 1)
        if (s.state === 'ok') { toast(`知识星球已连接${s.name ? '：' + s.name : ''}`); onDone() }
      } catch { /* keep polling */ }
    }, 2000)
    return () => window.clearTimeout(t)
  }, [open, st, tick, onDone, toast])
  async function start() {
    try { setSt(await api.post<LoginState>('/api/zsxq/login', { agree: true })) } catch (e) { toast((e as Error).message, 'error') }
  }
  const waiting = st && ['starting', 'waiting'].includes(st.state)
  return (
    <Dialog open={open} onClose={onClose} title="扫码连接知识星球" footer={<button className="btn" onClick={onClose}>{st?.state === 'ok' ? '完成' : '关闭'}</button>}>
      {!st || st.state === 'idle' ? (
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>工作台会在这台电脑上打开知识星球网页的登录页，把二维码显示在这里，你用微信扫一下就连上了。之后推长文只会存进星球的文章草稿箱，不会发布。</p>
          <label className="row small" style={{ gap: 8, alignItems: 'flex-start' }}>
            <input type="checkbox" checked={agree} onChange={e => setAgree(e.target.checked)} style={{ marginTop: 3 }} />
            <span>我同意知识星球的《用户协议》和《隐私政策》（登录星球网页时要勾选的那两项，工作台会替我在登录页点“同意”）</span>
          </label>
          <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn primary" disabled={!agree} onClick={start}>显示二维码</button></div>
        </div>
      ) : st.state === 'ok' ? (
        <div className="empty"><div className="empty-icon"><Ic.Check /></div><b>连上了{st.name ? `：${st.name}` : ''}</b></div>
      ) : st.state === 'failed' || st.state === 'expired' ? (
        <div className="stack">
          <div className="err">{st.state === 'expired' ? '二维码过期了。' : `没连上：${st.error || ''}`}</div>
          <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn primary" onClick={start}>再来一次</button></div>
        </div>
      ) : (
        <div className="stack" style={{ alignItems: 'center' }}>
          {st.has_qr ? <img className="zsxq-qr" src={`/api/zsxq/login/qr?t=${tick}`} alt="知识星球登录二维码" /> : <div className="zsxq-qr skeleton" />}
          <div className="small muted">{waiting && st.has_qr ? '用微信扫码，在手机上确认登录' : '正在打开登录页…'}</div>
        </div>
      )}
    </Dialog>
  )
}

/** Which 知识星球 group the article drafts go to (the number after groupId= in the web address). */
function ZsxqGroup({ group, onSaved }: { group?: { id: string; name: string }; onSaved: () => void }) {
  const toast = useToast()
  const [id, setId] = useState(group?.id || '')
  const [name, setName] = useState(group?.name || '')
  useEffect(() => { setId(group?.id || ''); setName(group?.name || '') }, [group?.id, group?.name])
  const dirty = id !== (group?.id || '') || name !== (group?.name || '')
  return (
    <div className="inline-form">
      <input className="input" style={{ maxWidth: 180 }} placeholder="星球编号" value={id} inputMode="numeric" onChange={e => setId(e.target.value.trim())} />
      <input className="input" style={{ maxWidth: 160 }} placeholder="星球名字（自己看的）" value={name} onChange={e => setName(e.target.value)} />
      {dirty && <button className="btn sm" onClick={async () => { try { await api.put('/api/zsxq/group', { id, name }); toast('星球已保存'); onSaved() } catch (e) { toast((e as Error).message, 'error') } }}>保存</button>}
    </div>
  )
}
