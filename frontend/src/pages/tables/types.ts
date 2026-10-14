// Спецификация маппинга — та же, что в backend/gosdoc/tables/spec.py.

export type Op = "copy" | "concat" | "split" | "date" | "fio_split" | "fio_join" | "money" | "dict" | "const";
export type Check = "required" | "inn" | "snils" | "ogrn" | "int" | "number" | "date";

export interface ColumnRule {
  target: string;
  op: Op;
  source?: string | null;
  sources?: string[];
  sep?: string;
  index?: number;
  in_format?: string;
  out_format?: string;
  part?: "last" | "first" | "middle";
  style?: "full" | "initials";
  map?: Record<string, string>;
  default?: string | null;
  value?: string;
}

export interface FilterRule {
  column: string;
  op: "eq" | "ne" | "in" | "not_in" | "contains" | "empty" | "not_empty";
  value: string | string[];
}

export interface Spec {
  columns: ColumnRule[];
  filter: FilterRule[];
  group_by: { keys: string[]; agg: Record<string, "sum" | "count" | "first"> } | null;
  validate: Record<string, Check[]>;
}

export interface UploadedFile {
  id: number;
  original_name: string;
}

export interface Job {
  id: number;
  status: "queued" | "running" | "done" | "failed";
  input_file_id: number | null;
  result: {
    rows_in?: number;
    rows_out?: number;
    rows_filtered?: number;
    rows_rejected?: number;
    result_file_id?: number;
    report_file_id?: number;
  };
  error: string | null;
  created_at: string;
}

export interface SavedMapping {
  id: number;
  name: string;
  spec: Spec;
  target_template_file_id: number;
  owner_id: number | null;
  shared: boolean;
}

export const OPS: Record<Op, string> = {
  copy: "Копировать",
  concat: "Объединить колонки",
  split: "Часть значения",
  date: "Дата",
  fio_split: "Часть ФИО",
  fio_join: "Собрать ФИО",
  money: "Сумма",
  dict: "По справочнику",
  const: "Постоянное значение",
};

export const CHECKS: Record<Check, string> = {
  required: "обязательное",
  inn: "ИНН",
  snils: "СНИЛС",
  ogrn: "ОГРН",
  int: "целое",
  number: "число",
  date: "дата",
};

export const FILTER_OPS: Record<FilterRule["op"], string> = {
  eq: "равно",
  ne: "не равно",
  in: "одно из (через ;)",
  not_in: "ни одно из (через ;)",
  contains: "содержит",
  empty: "пусто",
  not_empty: "не пусто",
};

export const MULTI_SOURCE: Op[] = ["concat", "fio_join"];
