export interface Body {
  title: string;
  greeting: string;
  paragraphs: string[];
  closing: string;
}

export interface Requisites {
  incoming_number: string;
  incoming_date: string | null;
  extended: boolean;
  attachments: string[];
  signer_position: string;
  signer_name: string;
  executor_phone: string;
}

export interface Version {
  id: number;
  version: number;
  body: Body;
  author_id: number | null;
  docx_file_id: number | null;
  pdf_file_id: number | null;
  meta: { prompt_version?: string; model_name?: string; fallback?: boolean };
  created_at: string;
}

export interface Letter {
  id: number;
  type: string;
  blank_id: number | null;
  outgoing_number: string | null;
  date: string | null;
  addressee: string;
  executor: string;
  deadline: string | null;
  status: "draft" | "approved";
  is_draft: boolean;
  requisites: Requisites;
  created_at: string;
}

export interface LetterDetail extends Letter {
  versions: Version[];
  checks: {
    deadline: string | null;
    days_left: number | null;
    number_ok: boolean | null;
    style: { paragraph: number; fragment: string; message: string }[];
  };
}

export interface LetterType {
  id: string;
  name: string;
}

export interface Blank {
  id: number;
  name: string;
}
