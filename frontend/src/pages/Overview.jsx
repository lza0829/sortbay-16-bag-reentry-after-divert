import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中…</div>
  const d = data.分布 || {}
  const bs = data.袋状态 || {}
  const dv = data.回流批次 || {}
  return (
    <div className="panel">
      <h2>总览</h2>
      <p className="muted">当前班次：{data.班次?.business_day} {data.班次?.name} · {data.班次?.sealed ? '已封班' : '未封班'}</p>
      <p className={data.对账一致 ? 'msg' : 'err'}>
        {data.对账一致 ? '对账一致' : `对账违规：${JSON.stringify(data.违规)}`}
      </p>
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>
      <div className="grid">
        {Object.entries(bs).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
        <div className="stat"><div className="n">{dv.进行中 ?? 0}</div><div className="l">回流进行中</div></div>
        <div className="stat"><div className="n">{dv.已承接 ?? 0}</div><div className="l">回流已承接</div></div>
      </div>
      <table>
        <thead><tr><th>口门</th><th>标题</th><th>状态</th><th>在口件数</th><th>开袋数</th><th>承接开袋</th></tr></thead>
        <tbody>
          {(data.口门 || []).map((c) => (
            <tr key={c.id}>
              <td>{c.code}</td><td>{c.title}</td><td>{c.status}</td><td>{c.在口件数}</td>
              <td>{c.开袋数}</td><td>{c.承接开袋数}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
