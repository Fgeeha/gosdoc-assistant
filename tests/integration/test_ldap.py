"""LDAP-провайдер на mock-сервере ldap3 (без сети)."""

import pytest
from gosdoc.core.auth import AuthError, LdapAuthProvider
from gosdoc.core.config import Settings
from ldap3 import MOCK_SYNC, Connection, Server
from ldap3.core.exceptions import LDAPSocketOpenError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

BASE = "ou=users,dc=fgau,dc=local"


def _factory(user_dn: str, password: str) -> Connection:
    conn = Connection(Server("mock"), user=user_dn, password=password, client_strategy=MOCK_SYNC)
    conn.strategy.add_entry(
        f"cn=sidorov,{BASE}",
        {
            "objectClass": ["person"],
            "cn": "sidorov",
            "sAMAccountName": "sidorov",
            "userPassword": "Ldap-password-1",
            "displayName": "Сидоров С.С.",
            "department": "Юридический отдел",
            "memberOf": [
                "CN=GosDoc-Users,OU=Groups,DC=fgau,DC=local",
                "CN=GosDoc-NPA,OU=Groups,DC=fgau,DC=local",
            ],
        },
    )
    conn.strategy.add_entry(
        f"cn=guest,{BASE}",
        {
            "objectClass": ["person"],
            "cn": "guest",
            "sAMAccountName": "guest",
            "userPassword": "Ldap-password-1",
            "memberOf": ["CN=Others,DC=fgau,DC=local"],
        },
    )
    return conn


def _settings() -> Settings:
    return Settings(
        auth_provider="ldap",
        ldap_url="ldaps://mock",
        ldap_user_dn_template=f"cn={{login}},{BASE}",
        ldap_search_base=BASE,
        ldap_user_filter="(sAMAccountName={login})",
        ldap_group_roles={"GosDoc-Users": "user", "GosDoc-NPA": "npa_admin"},
    )


@pytest.fixture
def provider() -> LdapAuthProvider:
    return LdapAuthProvider(_settings(), connection_factory=_factory)


def test_ldap_login_maps_groups_to_roles(
    provider: LdapAuthProvider, sessions: sessionmaker[Session]
) -> None:
    with sessions.begin() as db:
        user = provider.authenticate(db, "sidorov", "Ldap-password-1")
        assert user.roles == ["npa_admin", "user"]
        assert user.auth_source == "ldap" and user.password_hash is None
        assert user.department == "Юридический отдел"


def test_ldap_rejects_wrong_password_and_foreign_groups(
    provider: LdapAuthProvider, sessions: sessionmaker[Session]
) -> None:
    with sessions.begin() as db:
        with pytest.raises(AuthError, match="Неверный"):
            provider.authenticate(db, "sidorov", "wrong")
        with pytest.raises(AuthError, match="Неверный"):
            provider.authenticate(db, "sidorov", "")
        with pytest.raises(AuthError, match="Нет доступа"):
            provider.authenticate(db, "guest", "Ldap-password-1")


def test_ldap_unavailable_is_auth_error(sessions: sessionmaker[Session]) -> None:
    def down(user_dn: str, password: str) -> Connection:
        raise LDAPSocketOpenError("connection refused")

    provider = LdapAuthProvider(_settings(), connection_factory=down)
    with pytest.raises(AuthError, match="недоступна"), sessions.begin() as db:
        provider.authenticate(db, "sidorov", "Ldap-password-1")


def test_ldap_server_validates_certificate() -> None:
    import ssl

    conn = LdapAuthProvider(_settings())._default_connection("cn=x", "p")
    assert conn.server.tls.validate == ssl.CERT_REQUIRED
