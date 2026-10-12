"""LDAP-провайдер на mock-сервере ldap3 (без сети)."""

import pytest
from gosdoc.core.auth import AuthError, LdapAuthProvider
from gosdoc.core.config import Settings
from ldap3 import MOCK_SYNC, Connection, Server

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


@pytest.fixture
def provider() -> LdapAuthProvider:
    s = Settings(
        auth_provider="ldap",
        ldap_user_dn_template=f"cn={{login}},{BASE}",
        ldap_search_base=BASE,
        ldap_user_filter="(sAMAccountName={login})",
        ldap_group_roles={"GosDoc-Users": "user", "GosDoc-NPA": "npa_admin"},
    )
    return LdapAuthProvider(s, connection_factory=_factory)


def test_ldap_login_maps_groups_to_roles(provider, sessions) -> None:
    with sessions.begin() as db:
        user = provider.authenticate(db, "sidorov", "Ldap-password-1")
        assert user.roles == ["npa_admin", "user"]
        assert user.auth_source == "ldap" and user.password_hash is None
        assert user.department == "Юридический отдел"


def test_ldap_rejects_wrong_password_and_foreign_groups(provider, sessions) -> None:
    with sessions.begin() as db:
        with pytest.raises(AuthError, match="Неверный"):
            provider.authenticate(db, "sidorov", "wrong")
        with pytest.raises(AuthError, match="Неверный"):
            provider.authenticate(db, "sidorov", "")
        with pytest.raises(AuthError, match="Нет доступа"):
            provider.authenticate(db, "guest", "Ldap-password-1")
