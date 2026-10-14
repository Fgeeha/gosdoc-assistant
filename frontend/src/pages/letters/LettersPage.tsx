import { App, Button, Card, DatePicker, Form, Input, Select, Space, Table, Tag, Upload } from "antd";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api";
import type { Blank, Letter, LetterDetail, LetterType } from "./types";

export const fmtDate = (d: string | null) => (d ? dayjs(d).format("DD.MM.YYYY") : "—");

export default function LettersPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [letters, setLetters] = useState<Letter[]>([]);
  const [types, setTypes] = useState<LetterType[]>([]);
  const [blanks, setBlanks] = useState<Blank[]>([]);
  const [incoming, setIncoming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    const fail = (e: Error) => message.error(e.message);
    api<Letter[]>("/letters").then(setLetters).catch(fail);
    api<LetterType[]>("/letters/types").then(setTypes).catch(fail);
    api<Blank[]>("/letter-blanks").then(setBlanks).catch(fail);
  }, [message]);

  const typeName = Object.fromEntries(types.map((t) => [t.id, t.name]));

  const create = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      const letter = await api<LetterDetail>("/letters", {
        method: "POST",
        json: {
          type: v.type,
          description: v.description ?? "",
          addressee: v.addressee ?? "",
          blank_id: v.blank_id ?? null,
          incoming_file_id: incoming,
          requisites: {
            incoming_number: v.incoming_number ?? "",
            incoming_date: v.incoming_date ? v.incoming_date.format("YYYY-MM-DD") : null,
          },
        },
      });
      navigate(`/letters/${letter.id}`);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card title="Новое письмо">
        <Form form={form} layout="vertical" initialValues={{ type: "citizen_reply" }}>
          <Space wrap align="start" size="large">
            <Form.Item name="type" label="Тип письма" rules={[{ required: true }]}>
              <Select style={{ width: 320 }} options={types.map((t) => ({ value: t.id, label: t.name }))} />
            </Form.Item>
            <Form.Item name="blank_id" label="Бланк">
              <Select allowClear style={{ width: 260 }} placeholder="встроенный по ГОСТ" options={blanks.map((b) => ({ value: b.id, label: b.name }))} />
            </Form.Item>
            <Form.Item label="Входящее письмо (DOCX, PDF, TXT)">
              <Upload
                maxCount={1}
                accept=".docx,.pdf,.txt"
                onRemove={() => setIncoming(null)}
                customRequest={(opt) => {
                  const body = new FormData();
                  body.append("file", opt.file as File);
                  api<{ id: number }>("/files", { method: "POST", body })
                    .then((f) => {
                      setIncoming(f.id);
                      opt.onSuccess?.(f);
                    })
                    .catch((e) => {
                      message.error(e.message);
                      opt.onError?.(e);
                    });
                }}
              >
                <Button>Загрузить</Button>
              </Upload>
            </Form.Item>
            <Form.Item name="incoming_number" label="Вх. номер">
              <Input style={{ width: 140 }} />
            </Form.Item>
            <Form.Item name="incoming_date" label="Дата регистрации входящего" extra="Для обращений граждан от неё считается срок ответа">
              <DatePicker format="DD.MM.YYYY" />
            </Form.Item>
          </Space>
          <Form.Item name="addressee" label="Адресат (каждая строка — отдельной строкой реквизита)">
            <Input.TextArea rows={3} placeholder={"Петрову П.П.\nул. Садовая, д. 1, кв. 2,\nг. Энск, 123456"} />
          </Form.Item>
          <Form.Item name="description" label="Что нужно ответить или запросить">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Button type="primary" loading={busy} onClick={create}>
            Подготовить черновик
          </Button>
        </Form>
      </Card>

      <Card title="Мои письма">
        <Table
          rowKey="id"
          size="small"
          dataSource={letters}
          columns={[
            { title: "№", dataIndex: "id", width: 70, render: (id: number) => <Link to={`/letters/${id}`}>{id}</Link> },
            { title: "Тип", dataIndex: "type", render: (t: string) => typeName[t] ?? t },
            { title: "Адресат", dataIndex: "addressee", render: (a: string) => a.split("\n")[0] },
            { title: "Исх. №", dataIndex: "outgoing_number" },
            { title: "Срок ответа", dataIndex: "deadline", render: fmtDate },
            {
              title: "Статус",
              render: (_, l) => (l.is_draft ? <Tag color="orange">черновик</Tag> : <Tag color="green">утверждено</Tag>),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
