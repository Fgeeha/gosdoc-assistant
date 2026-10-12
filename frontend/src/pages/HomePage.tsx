import { Card, Col, Row, Tag, Typography } from "antd";

const MODULES = [
  { title: "Проверка документов", text: "Проверка ссылок на НПА и соответствия содержания нормам." },
  { title: "Подготовка писем", text: "Служебные письма по ГОСТ Р 7.0.97-2016 на бланке организации." },
  { title: "Перенос данных между таблицами", text: "Перенос данных из одной таблицы в шаблон другой с проверкой." },
];

export default function HomePage() {
  return (
    <>
      <Typography.Title level={3}>Модули</Typography.Title>
      <Row gutter={[16, 16]}>
        {MODULES.map((m) => (
          <Col xs={24} md={8} key={m.title}>
            <Card title={m.title} extra={<Tag>в разработке</Tag>}>
              {m.text}
            </Card>
          </Col>
        ))}
      </Row>
      <Typography.Paragraph type="secondary" style={{ marginTop: 24 }}>
        Все результаты, подготовленные автоматически, являются черновиками и требуют проверки сотрудником.
      </Typography.Paragraph>
    </>
  );
}
