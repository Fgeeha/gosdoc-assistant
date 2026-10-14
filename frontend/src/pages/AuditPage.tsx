import { Input, Table } from "antd";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { api } from "../api";

interface AuditRow {
  id: number;
  at: string;
  user_id: number | null;
  ip: string | null;
  action: string;
  object_type: string | null;
  object_id: string | null;
  details: Record<string, unknown>;
}

const PAGE = 50;

export default function AuditPage() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");

  useEffect(() => {
    const q = new URLSearchParams({ limit: String(PAGE), offset: String((page - 1) * PAGE) });
    if (action) q.set("action", action);
    api<AuditRow[]>(`/admin/audit?${q}`).then(setRows);
  }, [page, action]);

  return (
    <>
      <Input.Search
        placeholder="Фильтр по действию, например auth.login_failed"
        allowClear
        onSearch={(v) => { setPage(1); setAction(v.trim()); }}
        style={{ maxWidth: 420, marginBottom: 16 }}
      />
      <Table
        rowKey="id"
        dataSource={rows}
        size="small"
        pagination={{ current: page, pageSize: PAGE, onChange: setPage, total: page * PAGE + (rows.length === PAGE ? 1 : 0) }}
        columns={[
          { title: "Время", dataIndex: "at", render: (d: string) => dayjs(d).format("DD.MM.YYYY HH:mm:ss") },
          { title: "Пользователь", dataIndex: "user_id" },
          { title: "IP", dataIndex: "ip" },
          { title: "Действие", dataIndex: "action" },
          { title: "Объект", render: (_, r) => (r.object_type ? `${r.object_type} #${r.object_id}` : "") },
          { title: "Подробности", dataIndex: "details", render: (d) => <code>{JSON.stringify(d)}</code> },
        ]}
      />
    </>
  );
}
