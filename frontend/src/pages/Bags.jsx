import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Bags() {
  const [bags, setBags] = useState([])
  const [chutes, setChutes] = useState([])
  const [scans, setScans] = useState([])
  const [diverted, setDiverted] = useState([])
  const [chuteId, setChuteId] = useState('')
  const [bagId, setBagId] = useState('')
  const [scanId, setScanId] = useState('')
  const [divertId, setDivertId] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = async () => {
    setBags(await api('/封袋'))
    const cs = await api('/口门'); setChutes(cs)
    if (!chuteId && cs[0]) setChuteId(String(cs[0].id))
    const ss = await api('/扫描')
    setScans(ss.filter((x) => x.status === '已路由' && !x.divert_batch_id))
    setDiverted(ss.filter((x) => x.status === '已路由' && x.divert_batch_id))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const openBag = async () => {
    setErr(''); setMsg('')
    try {
      const b = await api('/封袋/开袋', { method: 'POST', body: JSON.stringify({ 口门编号: Number(chuteId) }) })
      setMsg(`已开袋 ${b.code}`); setBagId(String(b.id)); load()
    } catch (e) { setErr(e.message) }
  }
  const add = async () => {
    setErr(''); setMsg('')
    try {
      await api(`/封袋/${bagId}/入袋`, { method: 'POST', body: JSON.stringify({ 扫描编号: Number(scanId) }) })
      setMsg('入袋成功'); load()
    } catch (e) { setErr(e.message) }
  }
  const settle = async () => {
    setErr(''); setMsg('')
    try {
      const r = await api('/回流承接/入袋', { method: 'POST', body: JSON.stringify({ 扫描编号: Number(divertId) }) })
      setMsg(`${r.新开袋 ? '已新开回流承接袋' : '已并入回流承接袋'} ${r.袋.code}（原袋 ${r.原袋编号 ?? '-'}）`)
      load()
    } catch (e) { setErr(e.message) }
  }
  const seal = async () => {
    setErr(''); setMsg('')
    try {
      await api(`/封袋/${bagId}/封口`, { method: 'POST' })
      setMsg('封袋成功'); load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>封袋</h2>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>口门
          <select value={chuteId} onChange={(e) => setChuteId(e.target.value)}>
            {chutes.map((c) => <option key={c.id} value={c.id}>{c.code}</option>)}
          </select>
        </label>
        <button type="button" onClick={openBag}>开袋</button>
      </div>
      <div className="row">
        <label>袋编号<input value={bagId} onChange={(e) => setBagId(e.target.value)} /></label>
        <label>常规已路由扫描
          <select value={scanId} onChange={(e) => setScanId(e.target.value)}>
            <option value="">选择</option>
            {scans.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.barcode}</option>)}
          </select>
        </label>
        <button type="button" onClick={add}>入袋</button>
        <button type="button" className="secondary" onClick={seal}>封口</button>
      </div>
      <div className="row">
        <label>回流后再裁决件（强制新开袋）
          <select value={divertId} onChange={(e) => setDivertId(e.target.value)}>
            <option value="">选择</option>
            {diverted.map((s) => (
              <option key={s.id} value={s.id}>{s.id} · {s.barcode} · 批次{s.divert_batch_id}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={settle}>回流承接</button>
      </div>
      <table>
        <thead><tr><th>编号</th><th>袋码</th><th>类型</th><th>口门</th><th>状态</th><th>件数</th><th>原袋</th><th>回流批次</th></tr></thead>
        <tbody>
          {bags.map((b) => (
            <tr key={b.id}>
              <td>{b.id}</td><td>{b.code}</td><td>{b.kind}</td><td>{b.口门码}</td><td>{b.status}</td>
              <td>{b.件数}</td><td>{b.原袋码 || '-'}</td><td>{b.回流批次码 || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
