// Обёртка над fetch: cookie сессии, JSON, понятный текст ошибки.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, ...rest } = init;
  const res = await fetch(`/api${path}`, {
    credentials: "same-origin",
    ...rest,
    headers: json === undefined ? rest.headers : { "Content-Type": "application/json", ...rest.headers },
    body: json === undefined ? rest.body : JSON.stringify(json),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d: { msg: string }) => d.msg).join("; ")
      : body.detail;
    throw new ApiError(res.status, detail ?? `Ошибка сервера (${res.status})`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export interface Me {
  id: number;
  login: string;
  full_name: string;
  department: string | null;
  roles: string[];
  must_change_password: boolean;
}

export const ROLE_NAMES: Record<string, string> = {
  user: "Пользователь",
  manager: "Руководитель",
  npa_admin: "Администратор нормативной базы",
  sys_admin: "Администратор системы",
};
