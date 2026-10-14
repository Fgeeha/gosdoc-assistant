# 0005. Аутентификация: свои учётные записи + LDAP

**Контекст.** Keycloak в каталоге платформы нет. AD может появиться позже.

**Решение.** Интерфейс `AuthProvider.authenticate(login, password)`:

- `local` — пароль в `users.password_hash` (argon2id). После
  `AUTH_MAX_FAILED_ATTEMPTS` неудачных попыток учётная запись блокируется на
  `AUTH_LOCKOUT_MINUTES`. Пользователей создаёт `sys_admin`, первый вход требует
  смены пароля, минимальная длина пароля — `AUTH_MIN_PASSWORD_LENGTH`.
- `ldap` — bind пользователем через ldap3 (TLS с `CA_BUNDLE`), чтение групп,
  роли по таблице `LDAP_GROUP_ROLES`. Пользователь создаётся или обновляется в
  `users` с `auth_source='ldap'`. Пока это заготовка, она проверена тестом на
  mock-сервере ldap3.

Сессия — случайный токен (256 бит) в Redis с TTL `SESSION_TTL_MINUTES`, в
браузере — cookie `gosdoc_session` (httpOnly, SameSite=Strict, Secure в проде).
Выход и смена пароля удаляют сессию. Хранение ID сессии в Redis позволяет
отзывать её мгновенно — в отличие от JWT.

**Последствия.** Нет внешнего IdP. SSO появится вместе с LDAP/Kerberos или
OIDC-провайдером — для этого достаточно добавить реализацию `AuthProvider`.
