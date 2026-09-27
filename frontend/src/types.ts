// API type definitions shared by the workbench UI.

export interface ColumnMeta {
  name: string
  ordinal: number
  full_type: string
  udt_name: string
  not_null: boolean
  default_expr: string | null
  is_auto: boolean
  char_len: number | null
  num_precision: number | null
  num_scale: number | null
  enum_values: string[] | null
}

export interface ForeignKey {
  name: string
  columns: string[]
  foreign_schema: string
  foreign_table: string
  foreign_columns: string[]
}

export interface UniqueGroup {
  name: string
  columns: string[]
  is_primary: boolean
}

export type CheckRule =
  | { column: string; kind: 'allowed_values'; values: string[]; constraint: string }
  | { column: string; kind: 'range'; op: string; value: number; constraint: string }

export interface TableMeta {
  schema: string
  name: string
  columns: ColumnMeta[]
  primary_key: string[]
  foreign_keys: ForeignKey[]
  unique_groups: UniqueGroup[]
  check_rules: Record<string, CheckRule[]>
}

export interface TablesResponse {
  schema: string
  tables: TableMeta[]
  generation_order: string[]
  dependencies: Record<string, string[]>
  cycle_error: string | null
}

export interface ConflictInfo {
  kind: 'unique' | 'fk' | 'not_null' | 'check'
  table: string | null
  table_schema: string
  constraint: string
  columns: string[]
  values: unknown[]
  other_table: string | null
  detail: string
}

export type JobStatus = 'running' | 'committed' | 'dry_run' | 'failed'

export interface Job {
  job_id: string
  seed: number
  requested_counts: Record<string, number>
  dry_run: boolean
  status: JobStatus
  phase: string
  percent: number
  order: string[]
  generated: Record<string, number>
  samples: Record<string, Array<Record<string, unknown>>>
  conflict: ConflictInfo | null
  error: string | null
  traceback?: string
  batch_no: string | null
  started_at: string
  finished_at: string | null
  note: string
}

export interface Batch {
  id: number
  batch_no: string
  seed: number
  status: string
  table_counts: Record<string, number>
  note: string | null
  created_at: string
  row_registrations: number
  total_planned: number
}

export interface BatchDetail extends Batch {
  registered_per_table: Record<string, number>
  samples: Record<string, Array<Record<string, unknown>>>
}

export interface CleanupResult {
  batch_no: string
  deleted: Record<string, number>
  registrations_removed: number
  skipped_already_missing: Record<string, number>
}

export interface GenerationRequest {
  seed: number
  counts: Record<string, number>
  dry_run: boolean
  note?: string
}
