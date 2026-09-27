import type { BatchError } from "../api";

export default function ErrorBox({ error }: { error: BatchError }) {
  return (
    <div className="error-box">
      <div style={{ fontWeight: 600, marginBottom: 6 }}>
        ❌ 约束冲突 / 生成失败（{error.type}
        {error.sqlstate ? ` / ${error.sqlstate}` : ""}）
      </div>
      <div className="field">
        涉及的表：<b>{error.table ?? "—"}</b>
      </div>
      <div className="field">
        涉及的约束：<b>{error.constraint ?? "—"}</b>
      </div>
      <div className="field muted">{error.detail}</div>
      {error.traceback && (
        <details className="muted" style={{ marginTop: 8 }}>
          <summary style={{ cursor: "pointer" }}>堆栈</summary>
          <pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>{error.traceback}</pre>
        </details>
      )}
    </div>
  );
}
