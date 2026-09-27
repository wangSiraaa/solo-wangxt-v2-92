import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Batch, BatchDetail, CleanupResult } from '../types'

interface Props {
  refreshKey: number
  onToast: (msg: string, isErr?: boolean) => void
  onViewDetail: (detail: BatchDetail | null) => void
  activeDetail: BatchDetail | null
}

export default function BatchesPanel({
  refreshKey,
  onToast,
  onViewDetail,
  activeDetail,
}: Props) {
  const [batches, setBatches] = useState<Batch[]>([])
  const [loading, setLoading] = useState(false)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [lastCleanup, setLastCleanup] = useState<CleanupResult | null>(null)

  const load = () => {
    setLoading(true)
    api
      .batches()
      .then(setBatches)
      .catch((e: Error) => onToast(e.message, true))
      .finally(() => setLoading(false))
  }

  useEffect(load, [refreshKey])

  const view = async (batchNo: string) => {
    try {
      if (activeDetail?.batch_no === batchNo) {
        onViewDetail(null)
        return
      }
      const detail = await api.batchDetail(batchNo)
      onViewDetail(detail)
    } catch (e) {
      onToast((e as Error).message, true)
    }
  }

  const doCleanup = async (batchNo: string) => {
    try {
      const result = await api.cleanup(batchNo)
      setLastCleanup(result)
      onToast(
        `已清理批次 ${batchNo}：删除 ${Object.entries(result.deleted)
          .map(([t, n]) => `${t} ${n} 行`)
          .join('，')}；既有记录未受影响`
      )
      setConfirming(null)
      onViewDetail(null)
      load()
    } catch (e) {
      onToast((e as Error).message, true)
    }
  }

  return (
    <section className="panel">
      <h2>
        🗂 批次管理
        <button className="ghost small" style={{ marginLeft: 'auto', padding: '3px 10px' }} onClick={load} disabled={loading}>
          刷新
        </button>
      </h2>
      <p className="muted small">
        每个批次记录独立标识、种子与每张表的主键。清理只会删除该批次登记过的行，
        既有记录（如预置客户 <span className="mono">PREEXIST-0001</span>）永远不会被删除。
      </p>

      {batches.length === 0 && (
        <p className="muted small">暂无已提交批次。</p>
      )}

      {batches.map((b) => (
        <div className="batch-item" key={b.id}>
          <div className="top">
            <span className="batch-no">{b.batch_no}</span>
            <span className={`status-pill ${b.status}`}>{b.status}</span>
            <span className="muted small">seed={b.seed}</span>
            <span className="muted small">{b.created_at}</span>
          </div>
          <div className="muted small" style={{ marginTop: 4 }}>
            计划：
            {Object.entries(b.table_counts)
              .map(([t, n]) => `${t}×${n}`)
              .join('，')}
            {' · '}已登记 {b.row_registrations} 行
            {b.note ? ` · 备注：${b.note}` : ''}
          </div>
          <div className="actions" style={{ marginTop: 8 }}>
            <button className="ghost" onClick={() => view(b.batch_no)}>
              {activeDetail?.batch_no === b.batch_no ? '收起详情' : '查看'}
            </button>
            {confirming === b.batch_no ? (
              <>
                <span className="small warn-text">
                  确认仅删除本批次 {b.row_registrations} 行？
                </span>
                <button className="danger" onClick={() => doCleanup(b.batch_no)}>
                  确认清理
                </button>
                <button className="ghost" onClick={() => setConfirming(null)}>
                  取消
                </button>
              </>
            ) : (
              <button
                className="danger"
                disabled={b.status !== 'committed'}
                onClick={() => setConfirming(b.batch_no)}
              >
                仅清理本批次
              </button>
            )}
          </div>

          {activeDetail?.batch_no === b.batch_no && (
            <div style={{ marginTop: 10 }}>
              <h3 style={{ marginTop: 0 }}>在库登记与样例</h3>
              <table className="meta">
                <thead>
                  <tr>
                    <th>表</th>
                    <th>本批次登记行数</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(activeDetail.registered_per_table).map(
                    ([t, n]) => (
                      <tr key={t}>
                        <td className="mono">{t}</td>
                        <td>{n}</td>
                      </tr>
                    )
                  )}
                </tbody>
              </table>
              {Object.entries(activeDetail.samples).map(([t, rows]) => (
                <div key={t} style={{ marginTop: 8 }}>
                  <div className="muted small">{t}（样例，最多 10 行）</div>
                  <div className="samples-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          {rows[0] &&
                            Object.keys(rows[0]).map((c) => <th key={c}>{c}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row, i) => (
                          <tr key={i}>
                            {Object.values(row).map((v, j) => (
                              <td key={j} title={String(v)}>
                                {v === null ? (
                                  <span className="muted">NULL</span>
                                ) : typeof v === 'object' ? (
                                  JSON.stringify(v)
                                ) : (
                                  String(v)
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {lastCleanup && (
        <div className="small ok-text" style={{ marginTop: 8 }}>
          上次清理结果：
          {JSON.stringify(lastCleanup.deleted)}
          {Object.values(lastCleanup.skipped_already_missing).some(Boolean) && (
            <> ；部分登记行此前已不存在（已跳过）</>
          )}
        </div>
      )}
    </section>
  )
}
