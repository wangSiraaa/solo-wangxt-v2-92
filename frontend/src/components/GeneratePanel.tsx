import { useState } from "react";
import type { SchemaInfo } from "../api";

export default function GeneratePanel({
  schema,
  onGenerate,
  busy,
}: {
  schema: SchemaInfo;
  onGenerate: (seed: number, counts: Record<string, number>) => void;
  busy: boolean;
}) {
  const [seed, setSeed] = useState(42);
  const [counts, setCounts] = useState<Record<string, number>>(() =>
    Object.fromEntries(schema.tables.map((t) => [t.name, t.fk_targets.length ? 10 : 5]))
  );

  const total = schema.tables.reduce((s, t) => s + (counts[t.name] || 0), 0);

  return (
    <div className="panel">
      <h2>⚙️ 生成参数</h2>
      <div className="controls">
        <label>
          随机种子（相同种子 → 相同数据）
          <input
            type="number"
            value={seed}
            min={0}
            onChange={(e) => setSeed(Number(e.target.value))}
          />
        </label>
        <div className="row-counts">
          {schema.tables.map((t) => (
            <label key={t.name}>
              {t.name} 记录数
              {t.fk_targets.length > 0 && (
                <span className="muted">（依赖 {t.fk_targets.join(", ")}）</span>
              )}
              <input
                type="number"
                min={0}
                value={counts[t.name] ?? 0}
                onChange={(e) =>
                  setCounts((c) => ({ ...c, [t.name]: Number(e.target.value) }))
                }
              />
            </label>
          ))}
        </div>
        <button
          disabled={busy || total === 0}
          onClick={() => onGenerate(seed, counts)}
        >
          {busy ? "生成中…" : `🚀 生成 ${total} 行`}
        </button>
      </div>
    </div>
  );
}
