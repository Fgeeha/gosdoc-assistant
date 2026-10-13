import { Button, Input, InputNumber, Select, Space, Table, Tag, Typography } from "antd";
import { CHECKS, FILTER_OPS, MULTI_SOURCE, OPS, type Check, type ColumnRule, type FilterRule, type Spec } from "./types";

interface Props {
  spec: Spec;
  sourceColumns: string[];
  confidence?: Record<string, number>;
  origin?: Record<string, "heuristic" | "llm" | "none">;
  onChange: (spec: Spec) => void;
}

const asOptions = (values: string[]) => values.map((v) => ({ value: v, label: v }));
const opOptions = Object.entries(OPS).map(([value, label]) => ({ value, label }));
const checkOptions = Object.entries(CHECKS).map(([value, label]) => ({ value, label }));
const filterOptions = Object.entries(FILTER_OPS).map(([value, label]) => ({ value, label }));

// «м=Мужской; ж=Женский» ⇄ {м: "Мужской", ж: "Женский"}
const mapToText = (map?: Record<string, string>) =>
  Object.entries(map ?? {}).map(([k, v]) => `${k}=${v}`).join("; ");
const textToMap = (text: string) =>
  Object.fromEntries(
    text.split(";").map((p) => p.split("=").map((s) => s.trim())).filter(([k, v]) => k && v !== undefined),
  );

function OriginTag({ origin, confidence }: { origin?: string; confidence?: number }) {
  if (origin === "heuristic") return <Tag color="green">по названию {Math.round((confidence ?? 0) * 100)}%</Tag>;
  if (origin === "llm") return <Tag color="orange">предложено моделью — проверьте</Tag>;
  if (origin === "none") return <Tag color="red">не найдено — выберите</Tag>;
  return null;
}

function Params({ rule, update }: { rule: ColumnRule; update: (patch: Partial<ColumnRule>) => void }) {
  switch (rule.op) {
    case "concat":
      return <Input addonBefore="разделитель" value={rule.sep ?? " "} onChange={(e) => update({ sep: e.target.value })} />;
    case "split":
      return (
        <Space.Compact>
          <Input addonBefore="разделитель" value={rule.sep ?? " "} onChange={(e) => update({ sep: e.target.value })} />
          <InputNumber addonBefore="часть №" min={1} value={(rule.index ?? 0) + 1} onChange={(v) => update({ index: (v ?? 1) - 1 })} />
        </Space.Compact>
      );
    case "date":
      return <Input addonBefore="формат" value={rule.out_format ?? "%d.%m.%Y"} onChange={(e) => update({ out_format: e.target.value })} />;
    case "fio_split":
      return (
        <Select
          value={rule.part ?? "last"}
          onChange={(part) => update({ part })}
          options={[{ value: "last", label: "Фамилия" }, { value: "first", label: "Имя" }, { value: "middle", label: "Отчество" }]}
        />
      );
    case "fio_join":
      return (
        <Select
          value={rule.style ?? "full"}
          onChange={(style) => update({ style })}
          options={[{ value: "full", label: "Иванов Иван Иванович" }, { value: "initials", label: "Иванов И.И." }]}
        />
      );
    case "dict":
      return (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Input placeholder="м=Мужской; ж=Женский" defaultValue={mapToText(rule.map)} onBlur={(e) => update({ map: textToMap(e.target.value) })} />
          <Input placeholder="иначе (пусто — ошибка)" value={rule.default ?? ""} onChange={(e) => update({ default: e.target.value || null })} />
        </Space>
      );
    case "const":
      return <Input placeholder="значение" value={rule.value ?? ""} onChange={(e) => update({ value: e.target.value })} />;
    default:
      return null;
  }
}

export default function MappingEditor({ spec, sourceColumns, confidence, origin, onChange }: Props) {
  const setRule = (i: number, patch: Partial<ColumnRule>) =>
    onChange({ ...spec, columns: spec.columns.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  const setChecks = (target: string, checks: Check[]) =>
    onChange({ ...spec, validate: { ...spec.validate, [target]: checks } });
  const setFilter = (i: number, patch: Partial<FilterRule>) =>
    onChange({ ...spec, filter: spec.filter.map((f, j) => (j === i ? { ...f, ...patch } : f)) });

  return (
    <>
      <Table
        rowKey="target"
        size="small"
        pagination={false}
        dataSource={spec.columns}
        columns={[
          {
            title: "Колонка шаблона",
            dataIndex: "target",
            render: (t: string) => (
              <Space direction="vertical" size={0}>
                <Typography.Text strong>{t}</Typography.Text>
                <OriginTag origin={origin?.[t]} confidence={confidence?.[t]} />
              </Space>
            ),
          },
          {
            title: "Как заполнить",
            width: 190,
            render: (_, r, i) => (
              <Select style={{ width: "100%" }} value={r.op} options={opOptions} onChange={(op) => setRule(i, { op })} />
            ),
          },
          {
            title: "Из колонки источника",
            width: 260,
            render: (_, r, i) =>
              r.op === "const" ? null : MULTI_SOURCE.includes(r.op) ? (
                <Select mode="multiple" style={{ width: "100%" }} value={r.sources ?? []} options={asOptions(sourceColumns)} onChange={(sources) => setRule(i, { sources, source: null })} />
              ) : (
                <Select style={{ width: "100%" }} value={r.source ?? undefined} placeholder="выберите" options={asOptions(sourceColumns)} onChange={(source) => setRule(i, { source, sources: [] })} />
              ),
          },
          { title: "Параметры", render: (_, r, i) => <Params rule={r} update={(p) => setRule(i, p)} /> },
          {
            title: "Проверки",
            width: 200,
            render: (_, r) => (
              <Select mode="multiple" style={{ width: "100%" }} value={spec.validate[r.target] ?? []} options={checkOptions} onChange={(v) => setChecks(r.target, v)} />
            ),
          },
        ]}
      />
      <Typography.Title level={5} style={{ marginTop: 16 }}>
        Какие строки переносить
      </Typography.Title>
      <Space direction="vertical" style={{ width: "100%" }}>
        {spec.filter.map((f, i) => (
          <Space key={i} wrap>
            <Select style={{ width: 220 }} value={f.column} options={asOptions(sourceColumns)} onChange={(column) => setFilter(i, { column })} />
            <Select style={{ width: 200 }} value={f.op} options={filterOptions} onChange={(op) => setFilter(i, { op })} />
            {!["empty", "not_empty"].includes(f.op) && (
              <Input
                style={{ width: 240 }}
                value={Array.isArray(f.value) ? f.value.join("; ") : f.value}
                onChange={(e) =>
                  setFilter(i, { value: ["in", "not_in"].includes(f.op) ? e.target.value.split(";").map((s) => s.trim()) : e.target.value })
                }
              />
            )}
            <Button onClick={() => onChange({ ...spec, filter: spec.filter.filter((_, j) => j !== i) })}>Удалить</Button>
          </Space>
        ))}
        <Button onClick={() => onChange({ ...spec, filter: [...spec.filter, { column: sourceColumns[0], op: "eq", value: "" }] })}>
          Добавить условие
        </Button>
      </Space>
    </>
  );
}
