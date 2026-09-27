import { useEffect, useState } from "react";
import { api } from "../api";
import type { Batch } from "../api";

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "∅";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export default function SampleData({
  batch,
  tables,
}: {
  batch: Batch;
  tables: string[];
}) {
  const [active, setActive] = useState(tables[0] ?? "");
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (active) setActive((a) => (tables.includes(a) ? a : tables[0] ?? ""));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tables.join(",")]);

  useEffect(() => {
    if (!active || batch.status === "running") return;
    let cancelled = false;
    api
      .batchRows(batch.id, active)
      .then((d) => {
        if (!cancelled) {
          setRows(d.rows);
          setTotal(d.count_registered);
          setErr(null);
        }
      })
      .catch((e) => !cancelled && setErr(String(e)));
    return () => {
      cancelled = true;
    };
  }, [active, batch.id, batch.status]);

  if (tables.length === 0) return <div className="muted">本批次未生成数据</div>;

  const cols = rows.length > 0 ? Object.keys(rows[0]) : [];

  return (
    <div>
      <div className="tabs">
        {tables.map((t) => (
          <button
            key={t}
            className={t === active ? "active" : ""}
            onClick={() => setActive(t)}
          >
            {t}
          </button>
        ))}
      </div>
      {err ? (
        <div className="muted">{err}</div>
      ) : rows.length === 0 ? (
        <div className="muted">加载中…</div>
      ) : (
        <>
          <div className="muted" style={{ marginBottom: 6, fontSize: 12 }}>
            本批次在 {active} 登记 {total} 行，展示前 {rows.length} 行
          </div>
          <table className="data">
            <thead>
              <tr>
                {cols.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => (
                    <td key={c}>{fmt(r[c])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
