import { Alert, App, Button, Card, Form, Input } from "antd";
import { api } from "../api";

interface Values {
  old_password: string;
  new_password: string;
  confirm: string;
}

export default function ChangePasswordPage({ forced, onDone }: { forced?: boolean; onDone: () => void }) {
  const { message } = App.useApp();

  const submit = async ({ old_password, new_password }: Values) => {
    try {
      await api("/auth/password", { method: "POST", json: { old_password, new_password } });
      message.success("Пароль изменён. Войдите с новым паролем.");
      onDone();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: forced ? "100vh" : undefined }}>
      <Card title="Смена пароля" style={{ width: 420 }}>
        {forced && (
          <Alert type="info" showIcon style={{ marginBottom: 16 }}
            title="Перед началом работы нужно заменить временный пароль на свой." />
        )}
        <Form layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="old_password" label="Текущий пароль" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item name="new_password" label="Новый пароль" extra="Не короче 12 символов, буквы и цифры"
            rules={[{ required: true }, { min: 12, message: "Не короче 12 символов" }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="confirm" label="Повторите новый пароль" dependencies={["new_password"]}
            rules={[
              { required: true },
              ({ getFieldValue }) => ({
                validator: (_, v) =>
                  v === getFieldValue("new_password") ? Promise.resolve() : Promise.reject(new Error("Пароли не совпадают")),
              }),
            ]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            Сменить пароль
          </Button>
        </Form>
      </Card>
    </div>
  );
}
