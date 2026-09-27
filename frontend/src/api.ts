export interface ColumnInfo {
  name: string;
  type: string;
  nullable: boolean;
  is_pk: boolean;
  db_generated: boolean;
  references: string | null;
}

export interface TableInfo {
  name: string;
  columns: ColumnInfo[];
  pk_columns: string[];
  unique_columns: string[];
  fk_targets: string[];
  generation_order: number;
}

export interface SchemaInfo {
  tables: TableInfo[];
  generation_sequence: string[];
}

export interface TableProgress {
  inserted: number;
  total: number;
}

export interface ConstraintCheck {
  table: string;
  constraint: string;
  type: "not_null" | "unique" | "foreign_key" | string;
  status: "ok" | "violated";
  detail: string;
  violated_rows?: number;
}

export interface BatchError {
  type: string;
  sqlstate?: string | null;
  table?: string | null;
  constraint?: string | null;
  detail: string;
  traceback?: string;
}

export interface Batch {
  id: string;
  seed: number;
  params: { counts?: Record<string, number> };
  status: "running" | "done" | "failed";
  progress: {
    current_table?: string | null;
    percent?: number;
    tables?: Record<string, TableProgress>;
    samples?: Record<string, Record<string, unknown>[]>;
  };
  inserted_rows: number;
  constraint_checks?: ConstraintCheck[] | null;
  error?: BatchError | null;
  created_at?: string | null;
  finished_at?: string | null;
}

export interface CleanupResult {
  batch_id: string;
  deleted_rows: number;
  deleted_by_table: Record<string, number>;
  skipped_orphan_pk: number;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ? JSON.stringify(body.detail) : JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  schema: () => req<SchemaInfo>("/api/schema"),
  createBatch: (seed: number, counts: Record<string, number>) =>
    req<Batch>("/api/batches", {
      method: "POST",
      body: JSON.stringify({ seed, counts }),
    }),
  listBatches: () => req<Batch[]>("/api/batches"),
  getBatch: (id: string) => req<Batch>(`/api/batches/${id}`),
  cancelBatch: (id: string) =>
    req<{ cancelled: string }>(`/api/batches/${id}/cancel`, { method: "POST" }),
  deleteBatch: (id: string) =>
    req<CleanupResult>(`/api/batches/${id}`, { method: "DELETE" }),
  batchRows: (id: string, table: string) =>
    req<{ table: string; rows: Record<string, unknown>[]; count_registered: number }>(
      `/api/batches/${id}/rows/${table}`
    ),
};
