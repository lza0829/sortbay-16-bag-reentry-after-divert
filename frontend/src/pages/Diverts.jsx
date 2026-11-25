import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Diverts() {
  const [batches, setBatches] = useState([])
  const [scans, setScans] = useState([])
  const [scanId, setScanId] = useState('')
  const [batchId, setBatchId] = useState('')
  const [openBatch, setOpenBatch] = useState([])
  const [replay, setReplay] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = async () => {
    const bs = await api('/回流批次'); setBatches(bs)
    setOpenBatch(bs.filter((b) => b.status === '回流中'))
    const s = await api('/扫描')
    setScans(s.filter((x) => x.status === '已路由' || x.status === '已入袋'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const create = async () => {
    setErr(''); setMsg('')
    try {
      const payload = { 扫描编号: Number(scanId) }
      if (batchId) payload.批次编号 = Number(batchId)
      await api('/回流批次', { method: 'POST', body: JSON.stringify(payload) })
      setMsg(batchId ? '已并入回流批次' : '已建回流批次'); setScanId(''); load()
    } catch (e) { setErr(e.message) }
  }
  const doReplay = async (id) => {
    setErr(''); setMsg('')
    try { setReplay(await api(`/回流批次/${id}/重放`)) }
    catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>错分回流</h2>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>扫描
          <select value={scanId} onChange={(e) => setScanId(e.target.value)}>
            <option value="">选择</option>
            {scans.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.barcode} · {s.status}</option>)}
          </select>
        </label>
        <label>并入批次（可选）
          <select value={batchId} onChange={(e) => setBatchId(e.target.value)}>
            <option value="">新开批次</option>
            {openBatch.map((b) => <option key={b.id} value={b.id}>{b.id} · {b.code}</option>)}
          </select>
        </label>
        <button type="button" onClick={create}>建回流批次</button>
      </div>
      <table>
        <thead><tr><th>编号</th><th>批次码</th><th>状态</th><th>件数</th><th>待再裁决</th><th>已承接</th><th>承接袋</th><th>原袋</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>
          {batches.map((b) => (
            <tr key={b.id}>
              <td>{b.id}</td><td>{b.code}</td><td>{b.status}</td><td>{b.件数}</td>
              <td>{b.待再裁决}</td><td>{b.已承接件数}</td><td>{b.承接袋码 || '-'}</td>
              <td>{b.原袋编号 || '-'}</td><td>{b.created_at}</td>
              <td><button type="button" className="secondary" onClick={() => doReplay(b.id)}>重放</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {replay && (
        <div className="panel">
          <h3>批次 {replay.批次?.code} 重放对账</h3>
          <p className={replay.一致 ? 'msg' : 'err'}>
            {replay.一致 ? '一致' : `违规：${JSON.stringify(replay.违规)}`}
          </p>
          <table>
            <thead><tr><th>扫描</th><th>状态</th><th>当前袋</th><th>原袋</th><th>回流批次</th></tr></thead>
            <tbody>
              {(replay.成员 || []).map((s) => (
                <tr key={s.id}><td>{s.id}</td><td>{s.status}</td><td>{s.bag_id || '-'}</td><td>{s.origin_bag_id || '-'}</td><td>{s.divert_batch_id}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
