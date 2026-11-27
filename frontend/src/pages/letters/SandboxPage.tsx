import { Alert, App, Button, Card, Col, DatePicker, Descriptions, Empty, Form, Input, List, Row, Select, Space, Switch, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { useEffect, useState } from "react";
import { api } from "../../api";
import { fmtDate } from "./LettersPage";
import type { Blank, Body, LetterDetail, LetterType, Version } from "./types";

// Песочница: прогнать генерацию письма и посмотреть результат, ничего не сохраняя.

interface Result {
  body: Body;
  meta: Version["meta"];
  checks: LetterDetail["checks"];
  ms: number;
}

const SAMPLES: Record<string, Record<string, string>> = {
  citizen_reply: {
    addressee: "Петрову П.П.\nул. Садовая, д. 1, кв. 2,\nг. Энск, 123456",
    incoming_text:
      "Прошу принять меры по ремонту дороги по ул. Садовой между домами 1 и 15. Покрытие разрушено, после дождя образуются глубокие лужи, проезд скорой помощи затруднён.\nПетров П.П.",
    description: "Сообщить, что ремонт участка включён в план дорожных работ на 2027 год; до этого будет выполнен ямочный ремонт в течение месяца.",
  },
  request: {
    addressee: "Директору МУП «Водоканал»\nСидорову С.С.",
    incoming_text: "",
    description: "Запросить сведения об авариях на сетях водоснабжения в Центральном районе за 9 месяцев 2026 года: адреса, даты, длительность отключений. Срок — 10 рабочих дней, в электронном виде.",
  },
  cover: {
    addressee: "Министерство строительства\nЭнской области",
    incoming_text: "",
    description: "Направляем отчёт о ходе капитального ремонта многоквартирных домов за III квартал 2026 года на 12 л. в 1 экз. для рассмотрения.",
  },
};

export default function SandboxPage() {
  const { message } = App.useApp();
  const [types, setTypes] = useState<LetterType[]>([]);
  const [blanks, setBlanks] = useState<Blank[]>([]);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const type = Form.useWatch("type", form);

  useEffect(() => {
    api<LetterType[]>("/letters/types").then(setTypes).catch((e) => message.error(e.message));
    api<Blank[]>("/letter-blanks").then(setBlanks).catch(() => setBlanks([]));
  }, [message]);

  const requisites = () => {
    const v = form.getFieldsValue();
    return {
      type: v.type,
      blank_id: v.blank_id ?? null,
      addressee: v.addressee ?? "",
      outgoing_number: v.outgoing_number ?? "",
      date: v.date ? v.date.format("YYYY-MM-DD") : null,
      requisites: {
        incoming_number: v.incoming_number ?? "",
        incoming_date: v.incoming_date ? v.incoming_date.format("YYYY-MM-DD") : null,
        extended: !!v.extended,
      },
    };
  };

  const run = async () => {
    await form.validateFields();
    const v = form.getFieldsValue();
    setBusy(true);
    const started = performance.now();
    try {
      const r = await api<Omit<Result, "ms">>("/letters/sandbox", {
        method: "POST",
        json: { ...requisites(), description: v.description ?? "", incoming_text: v.incoming_text ?? "" },
      });
      setResult({ ...r, ms: Math.round(performance.now() - started) });
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // api() разбирает ответ как JSON, а здесь файл — поэтому fetch напрямую.
  const downloadDocx = async () => {
    if (!result) return;
    const res = await fetch("/api/letters/sandbox/docx", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...requisites(), body: result.body }),
    });
    if (!res.ok) return message.error(`Ошибка сервера (${res.status})`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await res.blob());
    a.download = `Тест письма ${dayjs().format("DD.MM HH-mm")}.docx`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const sample = () => form.setFieldsValue({ ...SAMPLES[type], incoming_date: type === "citizen_reply" ? dayjs() : null });
  const { body, checks, meta } = result ?? {};

  return (
    <Row gutter={24}>
      <Col xs={24} lg={10}>
        <Card title="Тест писем" size="small" extra={SAMPLES[type] && <Button size="small" onClick={sample}>Заполнить примером</Button>}>
          <Typography.Paragraph type="secondary">
            Проверка генерации и оформления. Ничего не сохраняется и не попадает в список писем.
          </Typography.Paragraph>
          <Form form={form} layout="vertical" initialValues={{ type: "citizen_reply" }}>
            <Space wrap align="start">
              <Form.Item name="type" label="Тип письма" rules={[{ required: true }]}>
                <Select style={{ width: 240 }} options={types.map((t) => ({ value: t.id, label: t.name }))} />
              </Form.Item>
              <Form.Item name="blank_id" label="Бланк">
                <Select allowClear style={{ width: 200 }} placeholder="встроенный по ГОСТ" options={blanks.map((b) => ({ value: b.id, label: b.name }))} />
              </Form.Item>
            </Space>
            <Form.Item name="incoming_text" label="Текст входящего письма">
              <Input.TextArea rows={5} placeholder="Вставьте текст обращения или запроса" />
            </Form.Item>
            <Form.Item name="description" label="Что нужно ответить или запросить">
              <Input.TextArea rows={3} />
            </Form.Item>
            <Form.Item name="addressee" label="Адресат">
              <Input.TextArea rows={3} />
            </Form.Item>
            <Space wrap align="start">
              <Form.Item name="outgoing_number" label="Исх. номер">
                <Input style={{ width: 130 }} />
              </Form.Item>
              <Form.Item name="date" label="Дата">
                <DatePicker format="DD.MM.YYYY" />
              </Form.Item>
              <Form.Item name="incoming_number" label="На №">
                <Input style={{ width: 110 }} />
              </Form.Item>
              <Form.Item name="incoming_date" label="от">
                <DatePicker format="DD.MM.YYYY" />
              </Form.Item>
              {type === "citizen_reply" && (
                <Form.Item name="extended" label="Срок продлён" valuePropName="checked">
                  <Switch />
                </Form.Item>
              )}
            </Space>
            <Button type="primary" loading={busy} onClick={run}>
              Сгенерировать
            </Button>
          </Form>
        </Card>
      </Col>
      <Col xs={24} lg={14}>
        {!result ? (
          <Card size="small">
            <Empty description="Заполните форму и нажмите «Сгенерировать»" />
          </Card>
        ) : (
          <Space direction="vertical" style={{ width: "100%" }}>
            {meta?.fallback && <Alert type="warning" showIcon message="Модель не вернула текст — показан шаблон структуры письма." />}
            <Card size="small" title="Черновик" extra={<Button size="small" onClick={downloadDocx}>Скачать DOCX</Button>}>
              <div style={{ fontFamily: "'Times New Roman', serif", fontSize: 15, lineHeight: 1.5 }}>
                {body?.title && <p style={{ fontStyle: "italic" }}>{body.title}</p>}
                {body?.greeting && <p style={{ textAlign: "center" }}>{body.greeting}</p>}
                {body?.paragraphs.map((p, i) => (
                  <p key={i} style={{ textIndent: "1.25cm", textAlign: "justify", margin: 0 }}>
                    {p}
                  </p>
                ))}
                {body?.closing && <p style={{ textIndent: "1.25cm", marginTop: 8 }}>{body.closing}</p>}
              </div>
            </Card>
            <Card size="small" title="Проверки и параметры">
              <Descriptions size="small" column={1}>
                <Descriptions.Item label="Модель">{meta?.model_name ?? "—"}</Descriptions.Item>
                <Descriptions.Item label="Версия промпта">{meta?.prompt_version ?? "—"}</Descriptions.Item>
                <Descriptions.Item label="Время генерации">{(result.ms / 1000).toFixed(1)} с</Descriptions.Item>
                <Descriptions.Item label="Срок ответа">
                  {fmtDate(checks?.deadline ?? null)}
                  {checks?.days_left != null && <Tag style={{ marginLeft: 8 }}>осталось {checks.days_left} дн.</Tag>}
                </Descriptions.Item>
                {checks?.number_ok === false && (
                  <Descriptions.Item label="Исх. номер">
                    <Tag color="red">не соответствует маске организации</Tag>
                  </Descriptions.Item>
                )}
              </Descriptions>
              <List
                size="small"
                header="Стиль"
                locale={{ emptyText: "Замечаний нет" }}
                dataSource={checks?.style ?? []}
                renderItem={(s) => (
                  <List.Item>
                    {s.message} {s.fragment && <Typography.Text code>{s.fragment}</Typography.Text>}
                  </List.Item>
                )}
              />
            </Card>
          </Space>
        )}
      </Col>
    </Row>
  );
}
