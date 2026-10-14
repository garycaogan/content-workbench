import { useState } from 'react'
import { startAuthentication, startRegistration } from '@simplewebauthn/browser'
import { api } from './api'
import * as Ic from './icons'

type Opts = { challenge_id: string; options: string }

function friendly(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e)
  if (/NotAllowed|cancel|abort/i.test(msg)) return '验证被取消了，再点一次就好'
  return msg
}

export function Login({ registered, onDone }: { registered: boolean; onDone: () => void }) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function register() {
    setBusy(true); setErr('')
    try {
      const o = await api.post<Opts>('/api/auth/register/options', { setup_code: code.trim() })
      const credential = await startRegistration({ optionsJSON: JSON.parse(o.options) })
      await api.post('/api/auth/register/verify', { challenge_id: o.challenge_id, credential, setup_code: code.trim(), label: deviceLabel() })
      onDone()
    } catch (e) { setErr(friendly(e)) } finally { setBusy(false) }
  }

  async function login() {
    setBusy(true); setErr('')
    try {
      const o = await api.post<Opts>('/api/auth/login/options')
      const credential = await startAuthentication({ optionsJSON: JSON.parse(o.options) })
      await api.post('/api/auth/login/verify', { challenge_id: o.challenge_id, credential })
      onDone()
    } catch (e) { setErr(friendly(e)) } finally { setBusy(false) }
  }

  return (
    <div className="login-wrap">
      <div className="card login-card">
        <span className="brand-mark"><Ic.Pen size={22} /></span>
        <h1>内容工作台</h1>
        {registered ? (
          <>
            <p>用这台设备的指纹、面容或设备密码登录。</p>
            <button className="btn primary lg" disabled={busy} onClick={login}>
              {busy ? <Ic.Loader size={16} /> : <Ic.Key size={16} />} 用 passkey 登录
            </button>
          </>
        ) : (
          <>
            <p>第一次使用，给这台设备注册一个 passkey。<br />初始化码只能用一次。</p>
            <input className="input" placeholder="初始化码" value={code} autoFocus
              onChange={e => setCode(e.target.value)} onKeyDown={e => e.key === 'Enter' && code.trim() && register()} />
            <button className="btn primary lg" disabled={busy || !code.trim()} onClick={register}>
              {busy ? <Ic.Loader size={16} /> : <Ic.Key size={16} />} 注册 passkey
            </button>
          </>
        )}
        {err && <div className="err" style={{ marginTop: 12 }}>{err}</div>}
      </div>
    </div>
  )
}

export function deviceLabel(): string {
  const ua = navigator.userAgent
  if (/iPhone/.test(ua)) return 'iPhone'
  if (/iPad/.test(ua)) return 'iPad'
  if (/Android/.test(ua)) return 'Android'
  if (/Mac/.test(ua)) return 'Mac'
  if (/Windows/.test(ua)) return 'Windows'
  return '设备'
}
