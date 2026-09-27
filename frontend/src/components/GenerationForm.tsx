import { useState } from 'react'

interface Props {
  running: boolean
  onGenerate: (opts: { seed: number; dryRun: boolean; note: string }) => void
}

export default function GenerationForm({ running, onGenerate }: Props) {
  const [seed, setSeed] = useState(42)
  const [note, setNote] = useState('')
  const [dryRun, setDryRun] = useState(false)

  return (
    <section className="panel">
      <h2>⚙️ 生成参数</h2>
      <div className="form-row">
        <label>随机种子</label>
        <input
          type="number"
          min={0}
          value={seed}
          onChange={(e) => setSeed(Number(e.target.value) || 0)}
        />
        <span className="muted small">
          相同种子 + 相同数量 ⇒ 生成相同的结构数据
        </span>
      </div>
      <div className="form-row">
        <label>批次备注</label>
        <input
          type="text"
          placeholder="例如：订单冒烟测试批次"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
      <div className="form-row">
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)}
          />
          试运行（执行约束检查并展示样例，但事务回滚，不写库）
        </label>
      </div>
      <div className="form-row" style={{ marginBottom: 0 }}>
        <button
          disabled={running}
          onClick={() => onGenerate({ seed, dryRun, note })}
        >
          {running ? '生成中…' : dryRun ? '开始试运行' : '生成并提交批次'}
        </button>
      </div>
    </section>
  )
}
