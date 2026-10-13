import { Button, Layout, Menu, Result, Spin, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { api, ApiError, type Me } from "./api";
import AuditPage from "./pages/AuditPage";
import ChangePasswordPage from "./pages/ChangePasswordPage";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import LetterEditor from "./pages/letters/LetterEditor";
import LettersPage from "./pages/letters/LettersPage";
import OrganizationPage from "./pages/OrganizationPage";
import TablesPage from "./pages/tables/TablesPage";
import UsersPage from "./pages/UsersPage";

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const location = useLocation();

  const reload = useCallback(() => {
    api<Me>("/auth/me")
      .then(setMe)
      .catch((e) => (e instanceof ApiError && e.status === 401 ? setMe(null) : Promise.reject(e)));
  }, []);
  useEffect(reload, [reload]);

  if (me === undefined) return <Spin fullscreen />;
  if (me === null) return <LoginPage onLogin={setMe} />;
  if (me.must_change_password) return <ChangePasswordPage forced onDone={() => setMe(null)} />;

  const isAdmin = me.roles.includes("sys_admin");
  const items = [
    { key: "/", label: <Link to="/">Главная</Link> },
    { key: "/letters", label: <Link to="/letters">Письма</Link> },
    { key: "/tables", label: <Link to="/tables">Таблицы</Link> },
    ...(isAdmin
      ? [
          { key: "/admin/users", label: <Link to="/admin/users">Пользователи</Link> },
          { key: "/admin/organization", label: <Link to="/admin/organization">Организация</Link> },
          { key: "/admin/audit", label: <Link to="/admin/audit">Журнал действий</Link> },
        ]
      : []),
    { key: "/password", label: <Link to="/password">Сменить пароль</Link> },
  ];

  const logout = async () => {
    await api("/auth/logout", { method: "POST" });
    setMe(null);
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Header style={{ display: "flex", alignItems: "center", gap: 24 }}>
        <Typography.Text strong style={{ color: "#fff", fontSize: 18, whiteSpace: "nowrap" }}>
          ГосДок Ассистент
        </Typography.Text>
        <Menu theme="dark" mode="horizontal" selectedKeys={[location.pathname.replace(/^\/letters\/.*/, "/letters")]} items={items} style={{ flex: 1 }} />
        <Typography.Text style={{ color: "#fff" }}>{me.full_name}</Typography.Text>
        <Button onClick={logout}>Выйти</Button>
      </Layout.Header>
      <Layout.Content style={{ padding: 24, maxWidth: 1200, width: "100%", margin: "0 auto" }}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/tables" element={<TablesPage />} />
          <Route path="/letters" element={<LettersPage />} />
          <Route path="/letters/:id" element={<LetterEditor me={me} />} />
          <Route path="/password" element={<ChangePasswordPage onDone={() => setMe(null)} />} />
          {isAdmin && <Route path="/admin/users" element={<UsersPage me={me} />} />}
          {isAdmin && <Route path="/admin/audit" element={<AuditPage />} />}
          {isAdmin && <Route path="/admin/organization" element={<OrganizationPage />} />}
          <Route path="/404" element={<Result status="404" title="Страница не найдена" />} />
          <Route path="*" element={<Navigate to="/404" replace />} />
        </Routes>
      </Layout.Content>
    </Layout>
  );
}
