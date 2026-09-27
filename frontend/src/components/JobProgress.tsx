import type { Job } from '../types'

interface Props {
  job: Job | null
}

const KIND_LABEL: Record<string, string> = {
  unique: '唯一约束冲突 (UNIQUE)',
  fk: '外键约束冲突 (FOREIGN KEY)',
  not_null: '非空约束冲突 (NOT NULL)',
  check: '检查约束冲突 (CHECK)',
}

export default function JobProgress({ job }: Props) {
  if (!job) {
    return (
      <section className="panel">
        <h2>📈 生成进度与约束检查</h2>
        <p className="muted">提交生成任务后，这里会实时展示进度、约束检查结果和样例数据。</p>
      </section>
    )
  }

  const totalPlanned = Object.values(job.requested_counts).reduce(
    (a, b) => a + b,
    0
  )
  const totalDone = Object.values(job.generated).reduce((a, b) => a + b, 0)

  return (
    <section className="panel">
      <h2>
        📈 生成进度
        <span className={`status-pill ${job.status}`}>
          {job.status === 'running'
            ? '进行中'
            : job.status === 'committed'
              ? '已提交'
              : job.status === 'dry_run'
                ? '试运行(未写库)'
                : '失败/回滚'}
        </span>
        {job.batch_no && (
          <span className="mono muted small">批次：{job.batch_no}</span>
        )}
      </h2>

      <div className="progress-track">
        <div
          className={`progress-bar ${job.status === 'failed' ? 'failed' : ''}`}
          style={{ width: `${job.percent}%` }}
        />
      </div>
      <div className="muted small">
        阶段：{job.phase} · 行 {totalDone}/{totalPlanned} · {job.percent}%
      </div>

      {job.order.length > 0 && (
        <>
          <h3>表生成情况（按父→子顺序）</h3>
          <table className="meta">
            <thead>
              <tr>
                <th>顺序</th>
                <th>表</th>
                <th>计划</th>
                <th>已生成</th>
                <th>检查</th>
              </tr>
            </thead>
            <tbody>
              {job.order.map((name, i) => {
                const planned = job.requested_counts[name] ?? 0
                const done = job.generated[name] ?? 0
                if (planned === 0 && done === 0) return null
                let check: { icon: string; cls: string; text: string }
                if (job.status === 'failed' && done < planned) {
                  check = { icon: '✗', cls: 'err-text', text: '中止（整批回滚）' }
                } else if (planned > 0 && done >= planned) {
                  check = { icon: '✓', cls: 'ok-text', text: '约束通过并写入' }
                } else if (job.status === 'dry_run') {
                  check = { icon: '✓', cls: 'warn-text', text: '检查通过（试运行）' }
                } else {
                  check = { icon: '…', cls: 'muted', text: '等待中' }
                }
                return (
                  <tr key={name}>
                    <td className="muted">{i + 1}</td>
                    <td className="mono">{name}</td>
                    <td>{planned}</td>
                    <td>{done}</td>
                    <td className={`small ${check.cls}`}>
                      <span className="icon">{check.icon}</span> {check.text}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}

      {job.conflict && (
        <div className="conflict-box" data-testid="conflict-box">
          <div className="title">
            ⚠ {KIND_LABEL[job.conflict.kind] ?? '约束冲突'}
          </div>
          <dl>
            <dt>涉及表</dt>
            <dd className="mono">
              {job.conflict.table_schema}.{job.conflict.table}
              {job.conflict.other_table && (
                <>
                  {' '}
                  <span className="muted">↔ 关联表</span>{' '}
                  <span className="mono">{job.conflict.other_table}</span>
                </>
              )}
            </dd>
            <dt>约束名</dt>
            <dd className="mono">{job.conflict.constraint}</dd>
            {job.conflict.columns.length > 0 && (
              <>
                <dt>涉及列</dt>
                <dd className="mono">{job.conflict.columns.join(', ')}</dd>
                <dt>冲突值</dt>
                <dd className="mono">
                  {JSON.stringify(job.conflict.values)}
                </dd>
              </>
            )}
          </dl>
          <div className="small">{job.conflict.detail}</div>
          <div className="small muted" style={{ marginTop: 6 }}>
            说明：本批次事务已整体回滚，数据库中未留下任何半成品数据。
          </div>
        </div>
      )}

      {job.error && !job.conflict && (
        <div className="conflict-box">
          <div className="title">⚠ 生成失败</div>
          <div className="small">{job.error}</div>
        </div>
      )}

      {(job.status === 'committed' || job.status === 'dry_run') && (
        <div className="check-line small ok-text">
          <span className="icon">✓</span>
          {job.status === 'committed'
            ? '主键、外键、非空、唯一与 CHECK 约束全部通过，已提交并登记批次。'
            : '主键、外键、非空、唯一与 CHECK 约束检查通过（试运行，已回滚未写库）。'}
        </div>
      )}
    </section>
  )
}
