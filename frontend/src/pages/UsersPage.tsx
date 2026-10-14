import { App, Button, Checkbox, Form, Input, Modal, Space, Switch, Table, Tag } from "antd";
import dayjs from "dayjs";
import { useCallback, useEffect, useState } from "react";
import { api, ROLE_NAMES, type Me } from "../api";

interface UserRow {
  id: number;
  login: string;
  full_name: string;
  department: string | null;
  roles: string[];
  auth_source: string;
  is_active: boolean;
  locked_until: string | null;
  last_login_at: string | null;
}

const roleOptions = Object.entries(ROLE_NAMES).map(([value, label]) => ({ value, label }));
const fmt = (d: string | null) => (d ? dayjs(d).format("DD.MM.YYYY HH:mm") : "—");

export default function UsersPage({ me }: { me: Me }) {
  const { message } = App.useApp();
  const [rows, setRows] = useState<UserRow[]>([]);
  const [editing, setEditing] = useState<UserRow | "new" | null>(null);
  const [form] = Form.useForm();

  const load = useCallback(() => {
    api<UserRow[]>("/admin/users").then(setRows).catch((e) => message.error(e.message));
  }, [message]);
  useEffect(load, [load]);

  const open = (row: UserRow | "new") => {
    setEditing(row);
    form.resetFields();
    if (row !== "new") form.setFieldsValue(row);
    else form.setFieldsValue({ roles: ["user"] });
  };

  const save = async () => {
    const v = await form.validateFields();
    try {
      if (editing === "new") {
        await api("/admin/users", { method: "POST", json: v });
      } else if (editing) {
        const { new_password, ...rest } = v;
        await api(`/admin/users/${editing.id}`, {
          method: "PATCH",
          json: { ...rest, ...(new_password ? { new_password } : {}) },
        });
      }
      message.success("Сохранено");
      setEditing(null);
      load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const unlock = async (row: UserRow) => {
    await api(`/admin/users/${row.id}`, { method: "PATCH", json: { unlock: true } });
    load();
  };

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" onClick={() => open("new")}>
          Добавить пользователя
        </Button>
      </Space>
      <Table
        rowKey="id"
        dataSource={rows}
        pagination={{ pageSize: 20 }}
        columns={[
          { title: "Логин", dataIndex: "login" },
          { title: "ФИО", dataIndex: "full_name" },
          { title: "Подразделение", dataIndex: "department" },
          {
            title: "Роли",
            dataIndex: "roles",
            render: (roles: string[]) => roles.map((r) => <Tag key={r}>{ROLE_NAMES[r] ?? r}</Tag>),
          },
          {
            title: "Состояние",
            render: (_, r) =>
              !r.is_active ? <Tag color="default">отключён</Tag>
                : r.locked_until && dayjs(r.locked_until).isAfter(dayjs()) ? <Tag color="red">заблокирован</Tag>
                : <Tag color="green">активен</Tag>,
          },
          { title: "Последний вход", dataIndex: "last_login_at", render: fmt },
          {
            title: "",
            render: (_, r) => (
              <Space>
                <Button size="small" onClick={() => open(r)}>Изменить</Button>
                {r.locked_until && <Button size="small" onClick={() => unlock(r)}>Разблокировать</Button>}
              </Space>
            ),
          },
        ]}
      />
      <Modal
        open={editing !== null}
        title={editing === "new" ? "Новый пользователь" : "Изменение пользователя"}
        onOk={save}
        onCancel={() => setEditing(null)}
        okText="Сохранить"
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item name="login" label="Логин" rules={[{ required: true, pattern: /^[A-Za-z0-9._-]+$/, message: "Латиница, цифры, . _ -" }]}>
            <Input disabled={editing !== "new"} />
          </Form.Item>
          <Form.Item name="full_name" label="ФИО" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="department" label="Подразделение">
            <Input />
          </Form.Item>
          <Form.Item name="roles" label="Роли" rules={[{ required: true, message: "Выберите хотя бы одну роль" }]}>
            <Checkbox.Group options={roleOptions} />
          </Form.Item>
          {editing !== "new" && editing?.id !== me.id && (
            <Form.Item name="is_active" label="Учётная запись активна" valuePropName="checked">
              <Switch />
            </Form.Item>
          )}
          <Form.Item
            name={editing === "new" ? "password" : "new_password"}
            label={editing === "new" ? "Временный пароль" : "Новый временный пароль"}
            extra="Пользователь сменит его при первом входе"
            rules={editing === "new" ? [{ required: true, min: 12, message: "Не короче 12 символов" }] : [{ min: 12 }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
