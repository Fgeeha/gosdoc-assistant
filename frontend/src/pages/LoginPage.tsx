import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { useState } from "react";
import { api, type Me } from "../api";

export default function LoginPage({ onLogin }: { onLogin: (me: Me) => void }) {
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);

  const submit = async (values: { login: string; password: string }) => {
    setLoading(true);
    setError(undefined);
    try {
      onLogin(await api<Me>("/auth/login", { method: "POST", json: values }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "100vh", background: "#f0f2f5" }}>
      <Card style={{ width: 380 }}>
        <Typography.Title level={3} style={{ textAlign: "center" }}>
          ГосДок Ассистент
        </Typography.Title>
        {error && <Alert type="error" title={error} showIcon style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item name="login" label="Логин" rules={[{ required: true, message: "Введите логин" }]}>
            <Input autoFocus autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="Пароль" rules={[{ required: true, message: "Введите пароль" }]}>
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            Войти
          </Button>
        </Form>
      </Card>
    </div>
  );
}
