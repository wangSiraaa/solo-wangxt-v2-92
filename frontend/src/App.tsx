import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { BatchDetail, Job, TablesResponse } from './types'
import SchemaPanel from './components/SchemaPanel'
import GenerationForm from './components/GenerationForm'
import JobProgress from './components/JobProgress'
import SamplesPanel from './components/SamplesPanel'
import BatchesPanel from './components/BatchesPanel'

interface Toast {
  id: number
  msg: string
  err: boolean
}

export default function App() {
  const [tables, setTables] = useState<TablesResponse | null>(null)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [job, setJob] = useState<Job | null>(null)
  const [batchDetail, setBatchDetail] = useState<BatchDetail | null>(null)
  const [batchRefresh, setBatchRefresh] = useState(0)
  const [toasts, setToasts] = useState<Toast[]>([])
  const pollRef = useRef<number | null>(null)

  const pushToast = useCallback((msg: string, err = false) => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, msg, err }])
    window.setTimeout(() => {
      setToasts((t) => t.filter((x) => x.id !== id))
    }, 6000)
  }, [])

  useEffect(() => {
    api
      .tables()
      .then((data) => {
        setTables(data)
        setCounts((prev) => {
          const next: Record<string, number> = {}
          for (const t of data.tables) next[t.name] = prev[t.name] ?? 0
          return next
        })
      })
      .catch((e: Error) => pushToast(e.message, true))
  }, [pushToast])

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  useEffect(() => stopPolling, [])

  const startPolling = (jobId: string) => {
    stopPolling()
    pollRef.current = window.setInterval(async () => {
      try {
        const fresh = await api.job(jobId)
        setJob(fresh)
        if (fresh.status !== 'running') {
          stopPolling()
          setBatchRefresh((k) => k + 1)
          if (fresh.status === 'failed') {
            pushToast(
              fresh.conflict
                ? `约束冲突：${fresh.conflict.table}.${fresh.conflict.constraint}（已回滚）`
                : `生成失败：${fresh.error ?? '未知错误'}`,
              true
            )
          } else if (fresh.status === 'committed') {
            pushToast(`批次 ${fresh.batch_no} 已提交`)
          }
        }
      } catch (e) {
        stopPolling()
        pushToast((e as Error).message, true)
      }
    }, 600)
  }

  const onGenerate = async ({
    seed,
    dryRun,
    note,
  }: {
    seed: number
    dryRun: boolean
    note: string
  }) => {
    try {
      const { job_id } = await api.generate({ seed, counts, dry_run: dryRun, note })
      setBatchDetail(null)
      const fresh = await api.job(job_id)
      setJob(fresh)
      startPolling(job_id)
    } catch (e) {
      pushToast((e as Error).message, true)
    }
  }

  const running = job?.status === 'running'

  return (
    <>
      <header className="app-header">
        <h1>🧪 合成数据生成工作台</h1>
        <p>
          FastAPI + SQLAlchemy 读取 PostgreSQL 表结构 · Faker 生成非真实姓名等测试值
          · 父表先于子表 · 相同种子可复现 · 批次级清理不触碰既有记录
        </p>
      </header>

      <div className="layout">
        <div>
          <GenerationForm running={running} onGenerate={onGenerate} />
          {tables ? (
            <SchemaPanel
              data={tables}
              counts={counts}
              onCountChange={(table, value) =>
                setCounts((c) => ({ ...c, [table]: value }))
              }
            />
          ) : (
            <section className="panel">
              <p className="muted">正在读取表结构…</p>
            </section>
          )}
        </div>

        <div>
          <JobProgress job={job} />
          <SamplesPanel job={job} batchDetail={batchDetail} />
          <BatchesPanel
            refreshKey={batchRefresh}
            onToast={pushToast}
            onViewDetail={setBatchDetail}
            activeDetail={batchDetail}
          />
        </div>
      </div>

      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.err ? 'err' : ''}`}>
          {t.msg}
        </div>
      ))}
    </>
  )
}
