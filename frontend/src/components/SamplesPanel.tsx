import { useState } from 'react'
import type { BatchDetail, Job } from '../types'

interface Props {
  job: Job | null
  batchDetail: BatchDetail | null
}

function SampleTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  if (rows.length === 0) return <p className="muted small">无样例</p>
  const columns = Array.from(
    rows.reduce<Set<string>>((set, row) => {
      Object.keys(row).forEach((k) => set.add(k))
      return set
    }, new Set())
  )
  return (
    <div className="samples-wrap">
      <table className="data">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => {
                const v = row[c]
                return (
                  <td key={c} title={String(v ?? '')}>
                    {v === null || v === undefined ? (
                      <span className="muted">NULL</span>
                    ) : typeof v === 'object' ? (
                      JSON.stringify(v)
                    ) : (
                      String(v)
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function SamplesPanel({ job, batchDetail }: Props) {
  const jobSamples = job?.samples ?? {}
  const batchSamples = batchDetail?.samples ?? {}
  const tableNames = Array.from(
    new Set([...Object.keys(jobSamples), ...Object.keys(batchSamples)])
  )
  const [active, setActive] = useState<string | null>(null)
  const current = active ?? tableNames[0]

  if (tableNames.length === 0) {
    return (
      <section className="panel">
        <h2>🔎 样例数据</h2>
        <p className="muted small">
          生成成功后可在此预览每张表最多 10 行样例（试运行同样展示，但不会写库）。
        </p>
      </section>
    )
  }

  return (
    <section className="panel">
      <h2>🔎 样例数据</h2>
      <div className="tabs">
        {tableNames.map((name) => (
          <button
            key={name}
            className={current === name ? 'active' : ''}
            onClick={() => setActive(name)}
          >
            {name}
          </button>
        ))}
      </div>
      {jobSamples[current] && (
        <>
          <h3 style={{ marginTop: 0 }}>
            本次生成 {job?.dry_run ? '（试运行预览）' : ''}
          </h3>
          <SampleTable rows={jobSamples[current]} />
        </>
      )}
      {batchSamples[current] && (
        <>
          <h3>批次在库数据</h3>
          <SampleTable rows={batchSamples[current]} />
        </>
      )}
    </section>
  )
}
