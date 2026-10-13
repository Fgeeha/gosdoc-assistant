import { App, Button, Card, DatePicker, Form, Input, List, Space, Switch, Table, Upload } from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Blank } from "./letters/types";

interface Holiday {
  date: string;
  is_workday: boolean;
  note: string;
}

export default function OrganizationPage() {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [holidays, setHolidays] = useState<Holiday[]>([]);
  const [blanks, setBlanks] = useState<Blank[]>([]);
  const [day, setDay] = useState<{ date: Dayjs | null; is_workday: boolean; note: string }>({ date: null, is_workday: false, note: "" });
  const fail = useCallback((e: Error) => message.error(e.message), [message]);

  const load = useCallback(() => {
    api<{ settings: Record<string, unknown> & { blank: { org_name: string; details: string[] } } }>("/admin/organization")
      .then((o) => form.setFieldsValue({ ...o.settings, org_name: o.settings.blank.org_name, details: o.settings.blank.details.join("\n") }))
      .catch(fail);
    api<Holiday[]>("/admin/holidays").then(setHolidays).catch(fail);
    api<Blank[]>("/letter-blanks").then(setBlanks).catch(fail);
  }, [form, fail]);
  useEffect(load, [load]);

  const save = async () => {
    const v = form.getFieldsValue();
    try {
      await api("/admin/organization", {
        method: "PUT",
        json: {
          blank: { org_name: v.org_name ?? "", details: (v.details ?? "").split("\n").filter((s: string) => s.trim()) },
          outgoing_number_pattern: v.outgoing_number_pattern ?? "",
          outgoing_number_example: v.outgoing_number_example ?? "",
          signer_position: v.signer_position ?? "",
          signer_name: v.signer_name ?? "",
        },
      });
      message.success("Сохранено");
    } catch (e) {
      fail(e as Error);
    }
  };

  const addHoliday = () =>
    day.date &&
    api("/admin/holidays", { method: "PUT", json: { ...day, date: day.date.format("YYYY-MM-DD") } }).then(load).catch(fail);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card title="Реквизиты организации для писем">
        <Form form={form} layout="vertical" style={{ maxWidth: 700 }}>
          <Form.Item name="org_name" label="Наименование организации (на бланке)">
            <Input />
          </Form.Item>
          <Form.Item name="details" label="Справочные данные (адрес, телефон, e-mail, ОГРН/ИНН — по строке)">
            <Input.TextArea rows={4} />
          </Form.Item>
          <Space wrap>
            <Form.Item name="signer_position" label="Должность подписанта по умолчанию">
              <Input style={{ width: 300 }} />
            </Form.Item>
            <Form.Item name="signer_name" label="Расшифровка подписи («И.О. Фамилия»)">
              <Input style={{ width: 240 }} />
            </Form.Item>
          </Space>
          <Space wrap>
            <Form.Item name="outgoing_number_pattern" label="Маска исходящего номера (регулярное выражение)" extra="Например: \d{2}-\d{2}/\d+">
              <Input style={{ width: 300 }} />
            </Form.Item>
            <Form.Item name="outgoing_number_example" label="Пример номера">
              <Input style={{ width: 200 }} />
            </Form.Item>
          </Space>
          <Button type="primary" onClick={save}>
            Сохранить
          </Button>
        </Form>
      </Card>

      <Card title="Бланки писем (DOCX-шаблоны)">
        <List
          size="small"
          dataSource={blanks}
          locale={{ emptyText: "Используется встроенный бланк по ГОСТ Р 7.0.97-2016" }}
          renderItem={(b) => (
            <List.Item actions={[<a key="off" onClick={() => api(`/letter-blanks/${b.id}`, { method: "DELETE" }).then(load).catch(fail)}>отключить</a>]}>
              {b.name}
            </List.Item>
          )}
        />
        <Upload
          accept=".docx"
          showUploadList={false}
          customRequest={(opt) => {
            const file = opt.file as File;
            const body = new FormData();
            body.append("file", file);
            api<{ id: number }>("/files", { method: "POST", body })
              .then((f) => api("/letter-blanks", { method: "POST", json: { name: file.name.replace(/\.docx$/i, ""), template_file_id: f.id } }))
              .then(() => {
                message.success("Бланк загружен");
                load();
              })
              .catch(fail);
          }}
        >
          <Button style={{ marginTop: 8 }}>Загрузить бланк</Button>
        </Upload>
      </Card>

      <Card title="Производственный календарь: переносы">
        <Space wrap style={{ marginBottom: 16 }}>
          <DatePicker format="DD.MM.YYYY" value={day.date} onChange={(d) => setDay({ ...day, date: d })} />
          <Switch checkedChildren="рабочий" unCheckedChildren="выходной" checked={day.is_workday} onChange={(is_workday) => setDay({ ...day, is_workday })} />
          <Input placeholder="примечание" value={day.note} onChange={(e) => setDay({ ...day, note: e.target.value })} />
          <Button onClick={addHoliday}>Добавить</Button>
        </Space>
        <Table
          rowKey="date"
          size="small"
          dataSource={holidays}
          pagination={false}
          columns={[
            { title: "Дата", dataIndex: "date", render: (d: string) => dayjs(d).format("DD.MM.YYYY") },
            { title: "День", dataIndex: "is_workday", render: (w: boolean) => (w ? "рабочий" : "выходной") },
            { title: "Примечание", dataIndex: "note" },
            { title: "", render: (_, h) => <a onClick={() => api(`/admin/holidays/${h.date}`, { method: "DELETE" }).then(load).catch(fail)}>удалить</a> },
          ]}
        />
        <div style={{ marginTop: 8, color: "#888" }}>Сб, вс и праздники ст. 112 ТК РФ учитываются автоматически.</div>
      </Card>
    </Space>
  );
}
