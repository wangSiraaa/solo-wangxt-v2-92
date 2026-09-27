import type { SchemaInfo, TableInfo } from "../api";

function ColumnBadges({ col }: { col: TableInfo["columns"][number] }) {
  return (
    <>
      {col.is_pk && <span className="badge badge-pk">主键 PK</span>}
      {col.references && <span className="badge badge-fk">外键 → {col.references}</span>}
      {!col.nullable && !col.is_pk && (
        <span className="badge badge-nn">非空</span>
      )}
      {col.db_generated && <span className="badge badge-db">数据库生成</span>}
    </>
  );
}

export default function SchemaPanel({ schema }: { schema: SchemaInfo }) {
  return (
    <div className="panel">
      <h2>📋 允许的业务表结构（SQLAlchemy 反射）</h2>
      <div className="seq">
        <span className="muted">生成顺序（父表 → 子表）：</span>
        {schema.generation_sequence.map((name, i) => (
          <span key={name} style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            {i > 0 && <span className="arrow">→</span>}
            <span className="tbl">
              {i + 1}. {name}
            </span>
          </span>
        ))}
      </div>
      <div className="grid-2">
        {schema.tables.map((t) => (
          <div key={t.name}>
            <div style={{ margin: "6px 0" }}>
              <b>{t.name}</b>
              <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>
                {t.columns.length} 列
              </span>
              {t.unique_columns.length > 0 && (
                <span className="badge badge-uq" style={{ marginLeft: 8 }}>
                  唯一: {t.unique_columns.join(", ")}
                </span>
              )}
            </div>
            <table className="meta">
              <thead>
                <tr>
                  <th>列</th>
                  <th>类型</th>
                  <th>约束</th>
                </tr>
              </thead>
              <tbody>
                {t.columns.map((c) => (
                  <tr key={c.name}>
                    <td>
                      {c.name}
                      {!c.nullable && ""}
                    </td>
                    <td className="muted">{c.type}</td>
                    <td>
                      <ColumnBadges col={c} />
                      {c.nullable && !c.db_generated && (
                        <span className="muted" style={{ fontSize: 11 }}>
                          可空
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
