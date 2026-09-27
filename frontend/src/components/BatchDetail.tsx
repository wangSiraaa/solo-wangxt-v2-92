import type { Batch } from "../api";
import ConstraintChecks from "./ConstraintChecks";
import ErrorBox from "./ErrorBox";
import ProgressPanel from "./ProgressPanel";
import SampleData from "./SampleData";

export default function BatchDetail({
  batch,
  onClose,
}: {
  batch: Batch;
  onClose: () => void;
}) {
  const tables = Object.keys(batch.params.counts ?? {}).filter(
    (t) => (batch.params.counts?.[t] ?? 0) > 0
  );
  return (
    <div className="panel">
      <h2>
        📦 批次详情
        <span className="batch-id" style={{ marginLeft: 4 }}>{batch.id}</span>
        <button className="ghost" style={{ marginLeft: "auto" }} onClick={onClose}>
          关闭
        </button>
      </h2>
      <div className="muted" style={{ marginBottom: 10, fontSize: 12 }}>
        seed = <b>{batch.seed}</b> · 参数 ={" "}
        {JSON.stringify(batch.params.counts)} · 插入 {batch.inserted_rows} 行 ·{" "}
        {batch.created_at}
      </div>
      <ProgressPanel batch={batch} />
      {batch.status === "failed" && batch.error && (
        <div style={{ marginTop: 14 }}>
          <ErrorBox error={batch.error} />
        </div>
      )}
      {batch.status === "done" && batch.constraint_checks && (
        <div style={{ marginTop: 16 }}>
          <h2>🔍 约束检查结果</h2>
          <ConstraintChecks checks={batch.constraint_checks} />
        </div>
      )}
      {batch.status === "done" && (
        <div style={{ marginTop: 16 }}>
          <h2>🧪 样例数据（回表读取本批次插入的行）</h2>
          <SampleData batch={batch} tables={tables} />
        </div>
      )}
    </div>
  );
}
