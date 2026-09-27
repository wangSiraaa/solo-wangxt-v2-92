import type { TableMeta, TablesResponse } from '../types'

interface Props {
  data: TablesResponse
  counts: Record<string, number>
  onCountChange: (table: string, value: number) => void
}

function ConstraintBadges({ table, column }: { table: TableMeta; column: string }) {
  const inPk = table.primary_key.includes(column)
  const fks = table.foreign_keys.filter((fk) => fk.columns.includes(column))
  const uqs = table.unique_groups.filter(
    (g) => !g.is_primary && g.columns.includes(column)
  )
  const checkRules = table.check_rules[column] ?? []
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {inPk && <span className="badge pk">主键 PK</span>}
      {fks.map((fk) => (
        <span
          key={fk.name}
          className="badge fk"
          title={`外键 → ${fk.foreign_table}(${fk.foreign_columns.join(', ')}) [${fk.name}]`}
        >
          FK→{fk.foreign_table}
        </span>
      ))}
      {uqs.map((g) => (
        <span key={g.name} className="badge uq" title={`唯一约束 ${g.name}`}>
          唯一{`(${g.columns.join(',')})`}
        </span>
      ))}
      {checkRules.map((rule, i) => {
        const text =
          rule.kind === 'allowed_values'
            ? rule.values.join('/')
            : `${rule.op} ${rule.value}`
        return (
          <span
            key={i}
            className="badge chk"
            title={`CHECK 约束 ${rule.constraint}`}
          >
            {text.length > 18 ? text.slice(0, 17) + '…' : text}
          </span>
        )
      })}
    </div>
  )
}

export default function SchemaPanel({ data, counts, onCountChange }: Props) {
  return (
    <section className="panel">
      <h2>📋 允许的表结构</h2>
      <p className="muted small">
        目标 schema：<span className="mono">{data.schema}</span>，共 {data.tables.length} 张表。
        生成顺序按外键拓扑排列（父表先于子表）。
      </p>

      <h3>生成顺序（父 → 子）</h3>
      <div className="order-chain">
        {data.generation_order.map((name, i) => (
          <span key={name} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {i > 0 && <span className="arrow">→</span>}
            <span className="tbl">{name}</span>
          </span>
        ))}
      </div>

      {data.cycle_error && (
        <div className="conflict-box">
          <div className="title">依赖循环</div>
          {data.cycle_error}
        </div>
      )}

      {data.tables
        .slice()
        .sort(
          (a, b) =>
            data.generation_order.indexOf(a.name) -
            data.generation_order.indexOf(b.name)
        )
        .map((table) => (
          <details className="table-block" key={table.name} open={data.tables.length <= 3}>
            <summary>
              <strong className="mono">{table.name}</strong>
              <span className="muted small">
                {table.columns.length} 列 · {table.foreign_keys.length} 外键
              </span>
              <span className="row-count-input">
                <label className="muted small">生成数量</label>
                <input
                  type="number"
                  min={0}
                  max={100000}
                  value={counts[table.name] ?? 0}
                  onClick={(e) => (e.target as HTMLInputElement).select()}
                  onChange={(e) =>
                    onCountChange(
                      table.name,
                      Math.max(0, Number(e.target.value) || 0)
                    )
                  }
                />
              </span>
            </summary>
            <div className="body">
              <table className="meta">
                <thead>
                  <tr>
                    <th style={{ width: '18%' }}>列</th>
                    <th style={{ width: '16%' }}>类型</th>
                    <th>约束</th>
                  </tr>
                </thead>
                <tbody>
                  {table.columns.map((col) => (
                    <tr key={col.name}>
                      <td className="mono">
                        {col.name}
                        {col.not_null && (
                          <span
                            className="badge nn"
                            style={{ marginLeft: 6 }}
                            title="NOT NULL"
                          >
                            非空
                          </span>
                        )}
                        {col.is_auto && (
                          <span className="muted small" style={{ marginLeft: 6 }}>
                            (自增，跳过)
                          </span>
                        )}
                      </td>
                      <td className="muted mono" title={col.default_expr ?? ''}>
                        {col.full_type}
                      </td>
                      <td>
                        <ConstraintBadges table={table} column={col.name} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ))}
    </section>
  )
}
