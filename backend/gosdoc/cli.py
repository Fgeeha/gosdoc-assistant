"""Служебные команды: gosdoc <команда>.

Используются в compose, Helm (initContainer/Job) и Airflow.
"""

import argparse
import getpass
import os
import sys
import time

from sqlalchemy import select

from gosdoc.core.auth import ROLES, AuthError, check_password_policy, hash_password
from gosdoc.core.config import get_settings
from gosdoc.core.db import User, get_sessionmaker
from gosdoc.core.files import cleanup_expired_files
from gosdoc.core.health import wait_for_dependencies
from gosdoc.core.logging import setup_logging
from gosdoc.core.storage import S3Storage, get_storage


def _create_user(a: argparse.Namespace) -> int:
    s = get_settings()
    password = os.environ.get(a.password_env, "") if a.password_env else getpass.getpass("Пароль: ")
    roles = a.roles.split(",")
    if set(roles) - set(ROLES):
        print(f"Допустимые роли: {', '.join(ROLES)}", file=sys.stderr)
        return 2
    with get_sessionmaker().begin() as db:
        if db.scalar(select(User.id).where(User.login == a.login)):
            print(f"Пользователь {a.login} уже есть")
            return 0 if a.if_missing else 1
        try:
            check_password_policy(password, s)
        except AuthError as e:
            print(e, file=sys.stderr)
            return 2
        db.add(
            User(
                login=a.login,
                full_name=a.full_name or a.login,
                roles=roles,
                organization_id=s.default_organization_id,
                auth_source="local",
                password_hash=hash_password(password),
                must_change_password=True,
                failed_attempts=0,
                is_active=True,
            )
        )
    print(f"Создан пользователь {a.login}")
    return 0


def _init_storage(timeout_s: float) -> int:
    storage = get_storage()
    if not isinstance(storage, S3Storage):
        return 0
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            storage.ensure_bucket()
            return 0
        except Exception as e:  # хранилище ещё не поднялось
            if time.monotonic() > deadline:
                print(f"S3 недоступно: {e}", file=sys.stderr)
                return 1
            time.sleep(3)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gosdoc")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wait-deps", help="ждать доступности всех зависимостей")
    w.add_argument("--timeout", type=float, default=300)
    i = sub.add_parser("init-storage", help="дождаться S3 и создать бакет, если его нет")
    i.add_argument("--timeout", type=float, default=300)
    sub.add_parser("cleanup-files", help="удалить файлы с истёкшим сроком хранения")
    u = sub.add_parser("create-user", help="создать локального пользователя")
    u.add_argument("--login", required=True)
    u.add_argument("--full-name")
    u.add_argument("--roles", default="user", help=f"через запятую: {','.join(ROLES)}")
    u.add_argument("--password-env", help="взять пароль из переменной окружения")
    u.add_argument("--if-missing", action="store_true", help="не ошибка, если уже есть")
    a = p.parse_args(argv)

    s = get_settings()
    setup_logging(s.log_level)
    if a.cmd == "wait-deps":
        return 0 if wait_for_dependencies(s, a.timeout) else 1
    if a.cmd == "init-storage":
        return _init_storage(a.timeout)
    if a.cmd == "cleanup-files":
        cleanup_expired_files(get_sessionmaker(), get_storage())
        return 0
    return _create_user(a)


if __name__ == "__main__":
    sys.exit(main())
