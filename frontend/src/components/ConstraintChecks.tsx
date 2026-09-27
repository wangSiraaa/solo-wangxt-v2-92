import type { ConstraintCheck } from "../api";

const TYPE_LABEL: Record<string, string> = {
  not_null: "非空",
  unique: "唯一",
  foreign_key: "外键",
};

export default function ConstraintChecks({ checks }: { checks: ConstraintCheck[] }) {
  const violated = checks.filter((c) => c.status === "violated");
  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        {violated.length === 0 ? (
          <span style={{ color: "var(--ok)" }}>✅ 全部 {checks.length} 项约束检查通过</span>
        ) : (
          <span style={{ color: "var(--err)" }}>
            ⚠️ {violated.length}/{checks.length} 项检查未通过
          </span>
        )}
      </div>
      {checks.map((c, i) => (
        <div key={i} className={`check-row check-${c.status}`}>
          <span className="check-icon">{c.status === "ok" ? "✓" : "✗"}</span>
          <div className="check-body">
            <span className="badge badge-uq">{TYPE_LABEL[c.type] ?? c.type}</span>
            <code>{c.constraint}</code>
            <span className="muted"> @ 表 {c.table}</span>
            <div className="muted" style={{ marginTop: 2 }}>
              {c.detail}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
