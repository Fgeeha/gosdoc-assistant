import { Alert, App, Button, Card, Space, Table, Tag, Typography, Upload } from "antd";
import dayjs from "dayjs";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api";

interface CheckJob {
  id: number;
  status: "queued" | "running" | "done" | "failed";
  input_file_id: number | null;
  result: { report_id?: number; findings?: number; critical?: number; refs_total?: number };
  error: string | null;
  created_at: string;
}

interface Finding {
  id: number;
  kind: "npa_ref" | "content";
  severity: "critical" | "warning" | "info";
  doc_fragment: string;
  npa_ref: string;
  norm: string;
  quote: string;
  message: string;
  recommendation: string;
  user_verdict: "accepted" | "rejected" | null;
}

interface Report {
  summary: Record<string, number | string>;
  result_docx_file_id: number | null;
  report_docx_file_id: number | null;
  findings: Finding[];
}

const SEVERITY = {
  critical: { color: "red", text: "критично" },
  warning: { color: "orange", text: "внимание" },
  info: { color: "blue", text: "к сведению" },
};
const STATUS = { queued: "в очереди", running: "проверяется", done: "готово", failed: "ошибка" };

export default function ChecksPage() {
  const { message } = App.useApp();
  const [jobs, setJobs] = useState<CheckJob[]>([]);
  const [names, setNames] = useState<Record<number, string>>({});
  const [open, setOpen] = useState<number | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const fail = useCallback((e: Error) => message.error(e.message), [message]);
  const load = useCallback(() => api<CheckJob[]>("/docs-check/jobs").then(setJobs).catch(fail), [fail]);
  useEffect(() => {
    load();
  }, [load]);

  const pending = jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [pending, load]);

  const show = (id: number) => {
    setOpen(id);
    api<Report>(`/docs-check/jobs/${id}/report`).then(setReport).catch(fail);
  };

  const verdict = async (f: Finding, v: "accepted" | "rejected") => {
    await api(`/docs-check/jobs/${open}/findings/${f.id}/verdict`, { method: "POST", json: { verdict: v } }).catch(fail);
    if (open) show(open);
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card title="Проверка документа по нормативной базе">
        <Typography.Paragraph type="secondary">
          DOCX, ODT, PDF (в том числе сканы), TXT. Проверяются ссылки на НПА (утратил силу, устаревшая редакция, неверная
          дата или статья) и соответствие содержания нормам — только с дословной цитатой нормы.
        </Typography.Paragraph>
        <Upload
          multiple
          accept=".docx,.odt,.pdf,.txt,.html"
          showUploadList={false}
          customRequest={(opt) => {
            const file = opt.file as File;
            const body = new FormData();
            body.append("file", file);
            api<{ id: number }>("/files", { method: "POST", body })
              .then((f) => {
                setNames((n) => ({ ...n, [f.id]: file.name }));
                return api("/docs-check/jobs", { method: "POST", json: { file_ids: [f.id] } });
              })
              .then(() => {
                message.success(`Проверка «${file.name}» запущена`);
                load();
              })
              .catch(fail);
          }}
        >
          <Button type="primary">Загрузить и проверить</Button>
        </Upload>
      </Card>

      <Card title="Мои проверки">
        <Table
          rowKey="id"
          size="small"
          dataSource={jobs}
          columns={[
            { title: "№", dataIndex: "id", width: 70 },
            { title: "Документ", render: (_, j) => (j.input_file_id && names[j.input_file_id]) || `файл ${j.input_file_id ?? "удалён"}` },
            { title: "Запущено", dataIndex: "created_at", render: (d: string) => dayjs(d).format("DD.MM.YYYY HH:mm") },
            { title: "Состояние", render: (_, j) => (j.status === "failed" ? <Typography.Text type="danger">{j.error}</Typography.Text> : STATUS[j.status]) },
            {
              title: "Замечания",
              render: (_, j) =>
                j.status === "done" && (
                  <Space>
                    {j.result.findings ?? 0}
                    {!!j.result.critical && <Tag color="red">критичных {j.result.critical}</Tag>}
                    <Button size="small" onClick={() => show(j.id)}>Открыть</Button>
                  </Space>
                ),
            },
          ]}
        />
      </Card>

      {open && report && (
        <Card
          title={`Отчёт о проверке № ${open}`}
          extra={
            <Space>
              {report.result_docx_file_id && <a href={`/api/files/${report.result_docx_file_id}/content`}>Документ с примечаниями</a>}
              {report.report_docx_file_id && <a href={`/api/files/${report.report_docx_file_id}/content`}>Отчёт DOCX</a>}
            </Space>
          }
        >
          <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="Черновик: замечания подготовлены автоматически и требуют проверки сотрудником." />
          <Table
            rowKey="id"
            size="small"
            pagination={false}
            dataSource={report.findings}
            expandable={{
              expandedRowRender: (f) => (
                <Space direction="vertical">
                  <Typography.Text type="secondary">Фрагмент документа: {f.doc_fragment}</Typography.Text>
                  {f.quote && <Typography.Text>Цитата нормы: «{f.quote}»</Typography.Text>}
                  {f.recommendation && <Typography.Text strong>Рекомендация: {f.recommendation}</Typography.Text>}
                </Space>
              ),
            }}
            columns={[
              { title: "", dataIndex: "severity", width: 110, render: (s: Finding["severity"]) => <Tag color={SEVERITY[s].color}>{SEVERITY[s].text}</Tag> },
              { title: "Тип", dataIndex: "kind", width: 110, render: (k: string) => (k === "npa_ref" ? "ссылка" : "содержание") },
              { title: "Замечание", dataIndex: "message" },
              { title: "Норма", dataIndex: "norm", width: 280 },
              {
                title: "Оценка",
                width: 190,
                render: (_, f) => (
                  <Space>
                    <Button size="small" type={f.user_verdict === "accepted" ? "primary" : "default"} onClick={() => verdict(f, "accepted")}>верно</Button>
                    <Button size="small" danger={f.user_verdict === "rejected"} onClick={() => verdict(f, "rejected")}>неверно</Button>
                  </Space>
                ),
              },
            ]}
          />
        </Card>
      )}
    </Space>
  );
}
