import type { Batch } from "../api";

export default function ProgressPanel({ batch }: { batch: Batch }) {
  const p = batch.progress;
  const tables = p.tables ?? {};
  return (
    <div className="progress-wrap">
      <div className="progress-bar">
        <div style={{ width: `${p.percent ?? 0}%` }} />
      </div>
      <div className="progress-meta">
        <span>
          {batch.status === "running" && p.current_table
            ? `正在生成 ${p.current_table}…`
            : batch.status === "done"
            ? "生成完成"
            : "生成失败"}
        </span>
        <span>{p.percent ?? 0}%</span>
      </div>
      {Object.entries(tables).length > 0 && (
        <div style={{ marginTop: 8, display: "flex", gap: 14, flexWrap: "wrap" }}>
          {Object.entries(tables).map(([name, t]) => (
            <span key={name} className="pill-count muted">
              {name}: <b style={{ color: "var(--text)" }}>{t.inserted}</b>/{t.total}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
