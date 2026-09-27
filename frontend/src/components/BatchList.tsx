import type { Batch, CleanupResult } from "../api";

export default function BatchList({
  batches,
  selectedId,
  onSelect,
  onDelete,
  busyId,
}: {
  batches: Batch[];
  selectedId?: string;
  onSelect: (b: Batch) => void;
  onDelete: (b: Batch) => Promise<CleanupResult | void>;
  busyId?: string;
}) {
  if (batches.length === 0)
    return <div className="muted">还没有批次，填写参数后点击「生成」。</div>;

  return (
    <div>
      {batches.map((b) => (
        <div key={b.id} className="batch-item">
          <div className="batch-head">
            <span
              className="batch-id"
              onClick={() => onSelect(b)}
              title="查看批次详情"
            >
              {b.id}
            </span>
            <span className="muted" style={{ fontSize: 12 }}>
              seed <b>{b.seed}</b> · {JSON.stringify(b.params.counts)}
            </span>
            <span className={`status status-${b.status}`}>
              {b.status === "running"
                ? `生成中 ${b.progress.percent ?? 0}%`
                : b.status === "done"
                ? `完成 · ${b.inserted_rows} 行`
                : "失败（约束冲突）"}
            </span>
            <span style={{ display: "flex", gap: 8 }}>
              <button className="ghost" onClick={() => onSelect(b)}>
                查看
              </button>
              <button
                className="danger"
                disabled={busyId === b.id || b.status === "running"}
                onClick={() => onDelete(b)}
                title="仅删除该批次插入的行，既有记录不受影响"
              >
                {busyId === b.id ? "清理中…" : "仅清理本批次"}
              </button>
            </span>
          </div>
          {selectedId === b.id && (
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              ↑ 当前查看的批次
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
