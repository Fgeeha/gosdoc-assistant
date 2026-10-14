import { App, Button, Card, Checkbox, DatePicker, Input, List, Modal, Space, Table, Tag, Typography, Upload } from "antd";
import type { UploadFile } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useCallback, useEffect, useState } from "react";
import { api, type Me } from "../../api";

interface Edition {
  id: number;
  effective_from: string;
  effective_to: string | null;
  status: "queued" | "indexed" | "failed";
  error: string | null;
}

interface Act {
  id: number;
  organization_id: number | null;
  label: string;
  repealed_at: string | null;
  repealed_note: string;
  editions: Edition[];
}

interface Change {
  at: string;
  act: string;
  change: string;
}

const CHANGE = { added: "добавлен", new_edition: "новая редакция", repealed: "утратил силу", restored: "восстановлен", reindexed: "переиндексация" };
const fmt = (d: string | null) => (d ? dayjs(d).format("DD.MM.YYYY") : "");

export default function NpaPage({ me }: { me: Me }) {
  const { message } = App.useApp();
  const [acts, setActs] = useState<Act[]>([]);
  const [changes, setChanges] = useState<Change[]>([]);
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [shared, setShared] = useState(false);
  const [from, setFrom] = useState<Dayjs | null>(null);
  const [repeal, setRepeal] = useState<{ act: Act; date: Dayjs | null; note: string } | null>(null);
  const canEdit = me.roles.includes("npa_admin") || me.roles.includes("sys_admin");
  const isSys = me.roles.includes("sys_admin");
  const fail = useCallback((e: Error) => message.error(e.message), [message]);

  const load = useCallback(() => {
    api<Act[]>("/npa/acts").then(setActs).catch(fail);
    api<Change[]>("/npa/changes").then(setChanges).catch(fail);
  }, [fail]);
  useEffect(load, [load]);

  const indexing = acts.some((a) => a.editions.some((e) => e.status === "queued"));
  useEffect(() => {
    if (!indexing) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [indexing, load]);

  const upload = async () => {
    const body = new FormData();
    files.forEach((f) => body.append("files", f.originFileObj as File));
    body.append("shared", String(shared));
    if (from) body.append("effective_from", from.format("YYYY-MM-DD"));
    try {
      const res = await api<{ name: string; ok: boolean; message: string }[]>("/npa/upload", { method: "POST", body });
      res.forEach((r) => (r.ok ? message.success(`${r.name}: ${r.message}`) : message.error(`${r.name}: ${r.message}`)));
      setFiles([]);
      load();
    } catch (e) {
      fail(e as Error);
    }
  };

  const saveRepeal = async () => {
    if (!repeal) return;
    await api(`/npa/acts/${repeal.act.id}`, {
      method: "PATCH",
      json: { repealed_at: repeal.date ? repeal.date.format("YYYY-MM-DD") : null, repealed_note: repeal.note },
    }).catch(fail);
    setRepeal(null);
    load();
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {canEdit && (
        <Card title="Загрузка актов и редакций">
          <Typography.Paragraph type="secondary">
            DOCX, HTML, PDF по одному, пакетом или zip-архивом. Вид, номер и дата акта определяются по заголовку; повторная
            загрузка акта — новая редакция (дата начала — «в ред. от …» в заголовке или указанная ниже).
          </Typography.Paragraph>
          <Space wrap align="start">
            <Upload multiple accept=".docx,.html,.htm,.pdf,.zip" fileList={files} beforeUpload={() => false} onChange={({ fileList }) => setFiles(fileList)}>
              <Button>Выбрать файлы</Button>
            </Upload>
            <DatePicker placeholder="редакция действует с" format="DD.MM.YYYY" value={from} onChange={setFrom} />
            {isSys && <Checkbox checked={shared} onChange={(e) => setShared(e.target.checked)}>общий акт для всех организаций</Checkbox>}
            <Button type="primary" disabled={!files.length} onClick={upload}>
              Загрузить
            </Button>
          </Space>
        </Card>
      )}

      <Card title="Нормативная база">
        <Table
          rowKey="id"
          size="small"
          dataSource={acts}
          columns={[
            { title: "Акт", dataIndex: "label" },
            { title: "", width: 120, render: (_, a) => (a.organization_id === null ? <Tag>общий</Tag> : <Tag color="blue">ведомственный</Tag>) },
            {
              title: "Редакции",
              render: (_, a) => (
                <Space direction="vertical" size={0}>
                  {a.editions.map((e) => (
                    <span key={e.id}>
                      с {fmt(e.effective_from)}
                      {e.effective_to ? ` по ${fmt(e.effective_to)}` : " (действующая)"}{" "}
                      {e.status === "queued" && <Tag color="processing">индексируется</Tag>}
                      {e.status === "failed" && <Tag color="error" title={e.error ?? ""}>ошибка</Tag>}
                    </span>
                  ))}
                </Space>
              ),
            },
            { title: "Утратил силу", render: (_, a) => (a.repealed_at ? <Tag color="red">с {fmt(a.repealed_at)}</Tag> : null) },
            ...(canEdit
              ? [
                  {
                    title: "",
                    render: (_: unknown, a: Act) => (
                      <Space>
                        <a onClick={() => setRepeal({ act: a, date: a.repealed_at ? dayjs(a.repealed_at) : null, note: a.repealed_note })}>статус</a>
                        <a onClick={() => api(`/npa/acts/${a.id}/reindex`, { method: "POST" }).then(load).catch(fail)}>переиндексировать</a>
                      </Space>
                    ),
                  },
                ]
              : []),
          ]}
        />
      </Card>

      <Card title="Журнал изменений">
        <List
          size="small"
          dataSource={changes.slice(0, 50)}
          renderItem={(c) => (
            <List.Item>
              {dayjs(c.at).format("DD.MM.YYYY HH:mm")} — {CHANGE[c.change as keyof typeof CHANGE] ?? c.change}: {c.act}
            </List.Item>
          )}
        />
      </Card>

      <Modal open={!!repeal} title="Статус акта" okText="Сохранить" onOk={saveRepeal} onCancel={() => setRepeal(null)}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <DatePicker placeholder="утратил силу с (пусто — действует)" format="DD.MM.YYYY" value={repeal?.date} onChange={(d) => repeal && setRepeal({ ...repeal, date: d })} />
          <Input.TextArea rows={3} placeholder="Каким актом отменён" value={repeal?.note} onChange={(e) => repeal && setRepeal({ ...repeal, note: e.target.value })} />
        </Space>
      </Modal>
    </Space>
  );
}
