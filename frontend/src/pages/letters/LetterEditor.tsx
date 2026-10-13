import { Alert, App, Button, Card, Col, DatePicker, Form, Input, List, Row, Select, Space, Switch, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type Me } from "../../api";
import { fmtDate } from "./LettersPage";
import type { Blank, LetterDetail } from "./types";

export default function LetterEditor({ me }: { me: Me }) {
  const { id } = useParams();
  const { message } = App.useApp();
  const [letter, setLetter] = useState<LetterDetail | null>(null);
  const [blanks, setBlanks] = useState<Blank[]>([]);
  const [pdfJob, setPdfJob] = useState<number | null>(null);
  const [form] = Form.useForm();

  const fill = useCallback(
    (l: LetterDetail) => {
      setLetter(l);
      const body = l.versions[l.versions.length - 1].body;
      form.setFieldsValue({
        ...body,
        paragraphs: body.paragraphs.join("\n\n"),
        addressee: l.addressee,
        executor: l.executor,
        outgoing_number: l.outgoing_number ?? "",
        date: l.date ? dayjs(l.date) : null,
        blank_id: l.blank_id,
        ...l.requisites,
        incoming_date: l.requisites.incoming_date ? dayjs(l.requisites.incoming_date) : null,
        attachments: (l.requisites.attachments ?? []).join("\n"),
      });
    },
    [form],
  );

  const load = useCallback(() => {
    api<LetterDetail>(`/letters/${id}`).then(fill).catch((e) => message.error(e.message));
  }, [id, fill, message]);
  useEffect(() => {
    load();
    api<Blank[]>("/letter-blanks").then(setBlanks).catch(() => setBlanks([]));
  }, [load]);

  // PDF собирается воркером — опрашиваем задачу до завершения.
  useEffect(() => {
    if (!pdfJob) return;
    const t = setInterval(async () => {
      const job = await api<{ status: string; error: string | null }>(`/letters/jobs/${pdfJob}`);
      if (job.status === "done" || job.status === "failed") {
        setPdfJob(null);
        if (job.status === "failed") message.error(job.error ?? "Ошибка конвертации");
        load();
      }
    }, 2000);
    return () => clearInterval(t);
  }, [pdfJob, load, message]);

  if (!letter) return null;
  const current = letter.versions[letter.versions.length - 1];
  const locked = letter.status === "approved";
  const lines = (s?: string) => (s ?? "").split("\n").map((x) => x.trim()).filter(Boolean);

  const save = async () => {
    const v = form.getFieldsValue();
    try {
      const l = await api<LetterDetail>(`/letters/${letter.id}`, {
        method: "PUT",
        json: {
          body: {
            title: v.title ?? "",
            greeting: v.greeting ?? "",
            closing: v.closing ?? "",
            paragraphs: (v.paragraphs ?? "").split(/\n\s*\n/).map((p: string) => p.trim()).filter(Boolean),
          },
          addressee: v.addressee ?? "",
          executor: v.executor ?? "",
          outgoing_number: v.outgoing_number ?? "",
          date: v.date ? v.date.format("YYYY-MM-DD") : null,
          blank_id: v.blank_id ?? null,
          requisites: {
            incoming_number: v.incoming_number ?? "",
            incoming_date: v.incoming_date ? v.incoming_date.format("YYYY-MM-DD") : null,
            extended: !!v.extended,
            attachments: lines(v.attachments),
            signer_position: v.signer_position ?? "",
            signer_name: v.signer_name ?? "",
            executor_phone: v.executor_phone ?? "",
          },
        },
      });
      fill(l);
      message.success(`Сохранена версия ${l.versions.length}`);
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const action = async (path: string) => {
    try {
      if (path === "pdf") {
        const r = await api<{ job_id: number }>(`/letters/${letter.id}/pdf`, { method: "POST" });
        setPdfJob(r.job_id);
      } else if (path === "approve") {
        fill(await api<LetterDetail>(`/letters/${letter.id}/approve`, { method: "POST" }));
      } else {
        await api(`/letters/${letter.id}/docx`, { method: "POST" });
        load();
      }
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const { checks } = letter;
  return (
    <Row gutter={24}>
      <Col xs={24} lg={16}>
        {letter.is_draft && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="Черновик. Текст подготовлен автоматически и требует проверки исполнителем и утверждения руководителем."
          />
        )}
        <Form form={form} layout="vertical" disabled={locked}>
          <Card title="Реквизиты" size="small">
            <Space wrap align="start">
              <Form.Item name="outgoing_number" label="Исх. номер">
                <Input style={{ width: 160 }} />
              </Form.Item>
              <Form.Item name="date" label="Дата">
                <DatePicker format="DD.MM.YYYY" />
              </Form.Item>
              <Form.Item name="incoming_number" label="На №">
                <Input style={{ width: 120 }} />
              </Form.Item>
              <Form.Item name="incoming_date" label="от">
                <DatePicker format="DD.MM.YYYY" />
              </Form.Item>
              {letter.type === "citizen_reply" && (
                <Form.Item name="extended" label="Срок продлён" valuePropName="checked">
                  <Switch />
                </Form.Item>
              )}
              <Form.Item name="blank_id" label="Бланк">
                <Select allowClear style={{ width: 220 }} placeholder="встроенный по ГОСТ" options={blanks.map((b) => ({ value: b.id, label: b.name }))} />
              </Form.Item>
            </Space>
            <Form.Item name="addressee" label="Адресат">
              <Input.TextArea rows={3} />
            </Form.Item>
          </Card>
          <Card title="Текст" size="small" style={{ marginTop: 16 }}>
            <Form.Item name="title" label="Заголовок к тексту">
              <Input />
            </Form.Item>
            <Form.Item name="greeting" label="Обращение">
              <Input />
            </Form.Item>
            <Form.Item name="paragraphs" label="Текст (абзацы разделяются пустой строкой)">
              <Input.TextArea rows={10} />
            </Form.Item>
            <Form.Item name="closing" label="Заключительный абзац">
              <Input.TextArea rows={2} />
            </Form.Item>
            <Form.Item name="attachments" label="Приложения (каждое с новой строки, «план на 2 л. в 1 экз.»)">
              <Input.TextArea rows={2} />
            </Form.Item>
          </Card>
          <Card title="Подпись и исполнитель" size="small" style={{ marginTop: 16 }}>
            <Space wrap>
              <Form.Item name="signer_position" label="Должность" extra="Пусто — из настроек организации">
                <Input style={{ width: 260 }} />
              </Form.Item>
              <Form.Item name="signer_name" label="Расшифровка подписи" extra="«И.О. Фамилия»">
                <Input style={{ width: 200 }} />
              </Form.Item>
              <Form.Item name="executor" label="Исполнитель">
                <Input style={{ width: 240 }} />
              </Form.Item>
              <Form.Item name="executor_phone" label="Телефон">
                <Input style={{ width: 160 }} />
              </Form.Item>
            </Space>
          </Card>
        </Form>
        <Space style={{ marginTop: 16 }} wrap>
          <Button type="primary" disabled={locked} onClick={save}>
            Сохранить новую версию
          </Button>
          <Button onClick={() => action("docx")}>Сформировать DOCX</Button>
          <Button loading={!!pdfJob} onClick={() => action("pdf")}>
            Сформировать PDF
          </Button>
          {me.roles.includes("manager") && letter.is_draft && (
            <Button onClick={() => action("approve")}>Утвердить</Button>
          )}
        </Space>
      </Col>
      <Col xs={24} lg={8}>
        <Card title="Проверки" size="small">
          <Typography.Paragraph>
            Срок ответа: <b>{fmtDate(checks.deadline)}</b>{" "}
            {checks.days_left !== null && (
              <Tag color={checks.days_left < 0 ? "red" : checks.days_left <= 5 ? "orange" : "green"}>
                {checks.days_left < 0 ? `просрочено на ${-checks.days_left} дн.` : `осталось ${checks.days_left} дн.`}
              </Tag>
            )}
          </Typography.Paragraph>
          {checks.number_ok === false && <Alert type="error" message="Исходящий номер не соответствует маске организации" />}
          <List
            size="small"
            header="Стиль"
            locale={{ emptyText: "Замечаний нет" }}
            dataSource={checks.style}
            renderItem={(s) => <List.Item>{s.message}</List.Item>}
          />
          <Typography.Text type="secondary">Ссылки на НПА в тексте будут проверяться модулем проверки документов.</Typography.Text>
        </Card>
        <Card title="Версии" size="small" style={{ marginTop: 16 }}>
          <List
            size="small"
            dataSource={[...letter.versions].reverse()}
            renderItem={(v) => (
              <List.Item>
                <Space direction="vertical" size={0}>
                  <span>
                    v{v.version} · {dayjs(v.created_at).format("DD.MM HH:mm")} ·{" "}
                    {v.author_id === null ? <Tag color="orange">черновик модели</Tag> : v.author_id === me.id ? "вы" : `пользователь ${v.author_id}`}
                  </span>
                  <Space>
                    {v.docx_file_id && <a href={`/api/files/${v.docx_file_id}/content`}>DOCX</a>}
                    {v.pdf_file_id && <a href={`/api/files/${v.pdf_file_id}/content`}>PDF</a>}
                    {v.id === current.id ? <Tag>текущая</Tag> : null}
                  </Space>
                </Space>
              </List.Item>
            )}
          />
        </Card>
      </Col>
    </Row>
  );
}
