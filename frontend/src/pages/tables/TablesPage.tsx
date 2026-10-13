import { Alert, App, Button, Card, Checkbox, Input, Modal, Select, Space, Table, Typography, Upload } from "antd";
import type { UploadRequestOption } from "@rc-component/upload/lib/interface";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import JobsTable from "./JobsTable";
import MappingEditor from "./MappingEditor";
import type { Job, SavedMapping, Spec, UploadedFile } from "./types";

interface Suggestion {
  source_columns: string[];
  preview: Record<string, string>[];
  spec: Spec;
  confidence: Record<string, number>;
  origin: Record<string, "heuristic" | "llm" | "none">;
}

interface Preview {
  rows: Record<string, string>[];
  issues: { row: number; status: string; reason: string }[];
  summary: Record<string, number>;
}

const ACCEPT = ".xlsx,.xls,.csv,.ods";

async function uploadFile(file: File): Promise<UploadedFile> {
  const body = new FormData();
  body.append("file", file);
  return api<UploadedFile>("/files", { method: "POST", body });
}

export default function TablesPage() {
  const { message } = App.useApp();
  const [sources, setSources] = useState<UploadedFile[]>([]);
  const [template, setTemplate] = useState<UploadedFile | null>(null);
  const [mappings, setMappings] = useState<SavedMapping[]>([]);
  const [mappingId, setMappingId] = useState<number | null>(null);
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [spec, setSpec] = useState<Spec | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [saving, setSaving] = useState<{ name: string; shared: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  const fail = useCallback((e: unknown) => message.error((e as Error).message), [message]);
  const loadJobs = useCallback(() => api<Job[]>("/tables/jobs").then(setJobs).catch(fail), [fail]);
  const loadMappings = useCallback(() => api<SavedMapping[]>("/tables/mappings").then(setMappings).catch(fail), [fail]);
  useEffect(() => {
    loadJobs();
    loadMappings();
  }, [loadJobs, loadMappings]);

  // Пока есть незавершённые задачи — обновляем список.
  const pending = jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(loadJobs, 2000);
    return () => clearInterval(t);
  }, [pending, loadJobs]);

  const names = useMemo(() => Object.fromEntries(sources.map((f) => [f.id, f.original_name])), [sources]);

  const onUpload = (target: "source" | "template") => (opt: UploadRequestOption) => {
    uploadFile(opt.file as File)
      .then((f) => {
        if (target === "source") setSources((prev) => [...prev, f]);
        else setTemplate(f);
        opt.onSuccess?.(f);
      })
      .catch((e) => {
        fail(e);
        opt.onError?.(e);
      });
  };

  const suggest = async () => {
    if (!sources.length || !template) return;
    setBusy(true);
    try {
      const s = await api<Suggestion>("/tables/suggest", {
        method: "POST",
        json: { source_file_id: sources[0].id, template_file_id: template.id },
      });
      setSuggestion(s);
      setSpec(s.spec);
      setMappingId(null);
      setPreview(null);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const useSaved = (id: number) => {
    const m = mappings.find((x) => x.id === id);
    if (!m) return;
    setMappingId(id);
    setSpec(m.spec);
    setSuggestion(null);
    setPreview(null);
  };

  const runPreview = () =>
    api<Preview>("/tables/preview", { method: "POST", json: { source_file_id: sources[0].id, spec } })
      .then(setPreview)
      .catch(fail);

  const save = async () => {
    if (!saving || !spec || !template) return;
    try {
      const m = await api<SavedMapping>("/tables/mappings", {
        method: "POST",
        json: { name: saving.name, shared: saving.shared, spec, template_file_id: template.id },
      });
      message.success("Маппинг сохранён");
      setSaving(null);
      await loadMappings();
      setMappingId(m.id);
    } catch (e) {
      fail(e);
    }
  };

  const run = async () => {
    try {
      const body = mappingId
        ? { source_file_ids: sources.map((f) => f.id), mapping_id: mappingId }
        : { source_file_ids: sources.map((f) => f.id), spec, template_file_id: template?.id };
      await api("/tables/jobs", { method: "POST", json: body });
      message.success("Перенос запущен");
      loadJobs();
    } catch (e) {
      fail(e);
    }
  };

  const sourceColumns = suggestion?.source_columns ?? [...new Set(spec?.columns.flatMap((c) => [c.source ?? "", ...(c.sources ?? [])]).filter(Boolean))];
  const canRun = sources.length > 0 && spec && (mappingId || template);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Перенос данных между таблицами
      </Typography.Title>

      <Card title="1. Файлы">
        <Space align="start" size="large" wrap>
          <Upload multiple accept={ACCEPT} customRequest={onUpload("source")} onRemove={(f) => setSources((p) => p.filter((x) => x.id !== f.response?.id))}>
            <Button>Исходные таблицы (XLSX, XLS, CSV, ODS)</Button>
          </Upload>
          <Upload maxCount={1} accept={ACCEPT} customRequest={onUpload("template")} onRemove={() => setTemplate(null)}>
            <Button>Целевой шаблон</Button>
          </Upload>
          <Select
            allowClear
            style={{ width: 300 }}
            placeholder="или сохранённый маппинг"
            value={mappingId ?? undefined}
            options={mappings.map((m) => ({ value: m.id, label: m.shared ? `${m.name} (общий)` : m.name }))}
            onChange={(id) => (id ? useSaved(id) : setMappingId(null))}
          />
        </Space>
      </Card>

      <Card
        title="2. Сопоставление колонок"
        extra={
          <Button type="primary" disabled={!sources.length || !template} loading={busy} onClick={suggest}>
            Предложить маппинг
          </Button>
        }
      >
        {spec ? (
          <Space direction="vertical" style={{ width: "100%" }}>
            {suggestion && (
              <Alert
                type="warning"
                showIcon
                message="Черновик: сопоставление предложено автоматически. Проверьте каждую колонку перед запуском."
              />
            )}
            <MappingEditor spec={spec} sourceColumns={sourceColumns} confidence={suggestion?.confidence} origin={suggestion?.origin} onChange={setSpec} />
            <Space>
              <Button disabled={!sources.length} onClick={runPreview}>
                Предпросмотр
              </Button>
              <Button disabled={!template} onClick={() => setSaving({ name: "", shared: false })}>
                Сохранить как шаблон маппинга
              </Button>
            </Space>
          </Space>
        ) : (
          <Typography.Text type="secondary">Загрузите таблицы и нажмите «Предложить маппинг» или выберите сохранённый.</Typography.Text>
        )}
      </Card>

      {preview && (
        <Card title={`Предпросмотр: первые строки (перенесено ${preview.summary.rows_out} из ${preview.summary.rows_in})`}>
          <Table
            size="small"
            rowKey={(_, i) => String(i)}
            dataSource={preview.rows}
            pagination={false}
            scroll={{ x: true }}
            columns={(spec?.columns ?? []).map((c) => ({ title: c.target, dataIndex: c.target }))}
          />
          {preview.issues.length > 0 && (
            <Table
              style={{ marginTop: 16 }}
              size="small"
              rowKey="row"
              dataSource={preview.issues}
              pagination={false}
              columns={[
                { title: "Строка", dataIndex: "row", width: 90 },
                { title: "Статус", dataIndex: "status", width: 140 },
                { title: "Причина", dataIndex: "reason" },
              ]}
            />
          )}
        </Card>
      )}

      <Card
        title="3. Перенос"
        extra={
          <Button type="primary" disabled={!canRun} onClick={run}>
            Перенести {sources.length > 1 ? `(${sources.length} файла)` : ""}
          </Button>
        }
      >
        <JobsTable jobs={jobs} names={names} />
      </Card>

      <Modal open={saving !== null} title="Сохранить маппинг" okText="Сохранить" onOk={save} onCancel={() => setSaving(null)}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Input placeholder="Название" value={saving?.name} onChange={(e) => setSaving((s) => s && { ...s, name: e.target.value })} />
          <Checkbox checked={saving?.shared} onChange={(e) => setSaving((s) => s && { ...s, shared: e.target.checked })}>
            Доступен всем сотрудникам организации
          </Checkbox>
          <Typography.Text type="secondary">Вместе с маппингом хранится только строка заголовков шаблона.</Typography.Text>
        </Space>
      </Modal>
    </Space>
  );
}
