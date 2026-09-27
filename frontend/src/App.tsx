import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Batch, CleanupResult, SchemaInfo } from "./api";
import BatchDetail from "./components/BatchDetail";
import BatchList from "./components/BatchList";
import GeneratePanel from "./components/GeneratePanel";
import SchemaPanel from "./components/SchemaPanel";

export default function App() {
  const [schema, setSchema] = useState<SchemaInfo | null>(null);
  const [schemaErr, setSchemaErr] = useState<string | null>(null);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [selectedId, setSelectedId] = useState<string | undefined>();
  const [busyGen, setBusyGen] = useState(false);
  const [busyDeleteId, setBusyDeleteId] = useState<string | undefined>();
  const [toast, setToast] = useState<{ text: string; err?: boolean } | null>(null);
  const pollRef = useRef<number | null>(null);

  const showToast = (text: string, err = false) => {
    setToast({ text, err });
    window.setTimeout(() => setToast(null), 4000);
  };

  const loadSchema = useCallback(async () => {
    try {
      setSchema(await api.schema());
      setSchemaErr(null);
    } catch (e) {
      setSchemaErr(String(e));
    }
  }, []);

  const loadBatches = useCallback(async () => {
    try {
      setBatches(await api.listBatches());
    } catch {
      /* 静默 */
    }
  }, []);

  useEffect(() => {
    loadSchema();
    loadBatches();
  }, [loadSchema, loadBatches]);

  // 有运行中的批次时轮询进度
  useEffect(() => {
    const anyRunning = batches.some((b) => b.status === "running");
    if (!anyRunning) {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    pollRef.current = window.setInterval(loadBatches, 700);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [batches, loadBatches]);

  const handleGenerate = async (seed: number, counts: Record<string, number>) => {
    setBusyGen(true);
    try {
      const b = await api.createBatch(seed, counts);
      setSelectedId(b.id);
      showToast(`批次 ${b.id} 已创建`);
      await loadBatches();
    } catch (e) {
      showToast(`创建失败：${String(e)}`, true);
    } finally {
      setBusyGen(false);
    }
  };

  const handleDelete = async (b: Batch): Promise<CleanupResult | void> => {
    if (
      !window.confirm(
        `确认仅清理批次 ${b.id} 插入的 ${b.inserted_rows} 行吗？\n` +
          "只删除该批次登记的记录，其他既有数据不会被删除。"
      )
    )
      return;
    setBusyDeleteId(b.id);
    try {
      const r = await api.deleteBatch(b.id);
      showToast(
        `已清理 ${r.deleted_rows} 行（${JSON.stringify(r.deleted_by_table)}），既有记录未动`
      );
      if (selectedId === b.id) setSelectedId(undefined);
      await loadBatches();
      return r;
    } catch (e) {
      showToast(`清理失败：${String(e)}`, true);
    } finally {
      setBusyDeleteId(undefined);
    }
  };

  const selected = batches.find((b) => b.id === selectedId);

  return (
    <div className="app">
      <h1>🧪 合成数据生成工作台</h1>
      <p className="subtitle">
        FastAPI + SQLAlchemy 反射表结构 · Faker 确定性生成 · PostgreSQL · 批次级精确清理
      </p>

      {schemaErr && (
        <div className="panel error-box">
          无法连接数据库或读取表结构：{schemaErr}
          <button className="ghost" style={{ marginLeft: 12 }} onClick={loadSchema}>
            重试
          </button>
        </div>
      )}

      {schema && (
        <>
          <SchemaPanel schema={schema} />
          <GeneratePanel schema={schema} onGenerate={handleGenerate} busy={busyGen} />
        </>
      )}

      {selected && <BatchDetail batch={selected} onClose={() => setSelectedId(undefined)} />}

      <div className="panel">
        <h2>🗂️ 批次列表（创建 / 查看 / 仅按本批次清理）</h2>
        <BatchList
          batches={batches}
          selectedId={selectedId}
          onSelect={(b) => setSelectedId(b.id)}
          onDelete={handleDelete}
          busyId={busyDeleteId}
        />
      </div>

      {toast && <div className={`toast ${toast.err ? "err" : ""}`}>{toast.text}</div>}
    </div>
  );
}
