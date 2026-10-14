import { Table, Tag, Typography } from "antd";
import dayjs from "dayjs";
import type { Job } from "./types";

const STATUS: Record<Job["status"], { color: string; text: string }> = {
  queued: { color: "default", text: "в очереди" },
  running: { color: "processing", text: "выполняется" },
  done: { color: "success", text: "готово" },
  failed: { color: "error", text: "ошибка" },
};

const download = (id?: number, text = "скачать") => (id ? <a href={`/api/files/${id}/content`}>{text}</a> : null);

export default function JobsTable({ jobs, names }: { jobs: Job[]; names: Record<number, string> }) {
  return (
    <Table
      rowKey="id"
      size="small"
      dataSource={jobs}
      pagination={{ pageSize: 10 }}
      columns={[
        { title: "№", dataIndex: "id", width: 70 },
        { title: "Файл", render: (_, j) => (j.input_file_id && names[j.input_file_id]) || `файл ${j.input_file_id ?? "удалён"}` },
        { title: "Запущено", dataIndex: "created_at", render: (d: string) => dayjs(d).format("DD.MM.YYYY HH:mm") },
        { title: "Состояние", dataIndex: "status", render: (s: Job["status"]) => <Tag color={STATUS[s].color}>{STATUS[s].text}</Tag> },
        {
          title: "Строки",
          render: (_, j) =>
            j.status === "done" ? (
              <Typography.Text>
                всего {j.result.rows_in}: перенесено {j.result.rows_out}, отфильтровано {j.result.rows_filtered},{" "}
                <Typography.Text type={j.result.rows_rejected ? "danger" : undefined}>с ошибками {j.result.rows_rejected}</Typography.Text>
              </Typography.Text>
            ) : (
              <Typography.Text type="danger">{j.error}</Typography.Text>
            ),
        },
        { title: "Результат", render: (_, j) => download(j.result.result_file_id) },
        { title: "Отчёт", render: (_, j) => download(j.result.report_file_id) },
      ]}
    />
  );
}
