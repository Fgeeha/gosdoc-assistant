"""Служебные команды: gosdoc <команда>.

Используются в compose, Helm (initContainer/Job) и Airflow.
"""

import argparse
import getpass
import logging
import os
import sys
import time

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from gosdoc.core.auth import ROLES, AuthError, check_password_policy, new_local_user, validate_roles
from gosdoc.core.config import Settings, get_settings
from gosdoc.core.db import User, get_sessionmaker
from gosdoc.core.files import cleanup_expired_files
from gosdoc.core.health import migrations_applied, wait_for_dependencies
from gosdoc.core.logging import setup_logging
from gosdoc.core.storage import S3Storage, get_storage


def _create_user(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.password_env:
        password = os.environ.get(args.password_env, "")
    else:
        password = getpass.getpass("Пароль: ")
    try:
        roles = validate_roles(args.roles.split(","))
        check_password_policy(password, settings)
    except (ValueError, AuthError) as e:
        print(e, file=sys.stderr)
        return 2
    with get_sessionmaker().begin() as db:
        if db.scalar(select(User.id).where(User.login == args.login)):
            print(f"Пользователь {args.login} уже есть")
            return 0 if args.if_missing else 1
        db.add(
            new_local_user(
                organization_id=settings.default_organization_id,
                login=args.login,
                full_name=args.full_name or args.login,
                roles=roles,
                password=password,
            )
        )
    print(f"Создан пользователь {args.login}")
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
        except (BotoCoreError, ClientError) as e:  # хранилище ещё не поднялось
            if time.monotonic() > deadline:
                print(f"S3 недоступно: {e}", file=sys.stderr)
                return 1
            time.sleep(3)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gosdoc")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wait-deps", help="ждать доступности всех зависимостей")
    w.add_argument("--timeout", type=float, default=300)
    w.add_argument(
        "--only",
        help="через запятую: postgres,redis,rabbitmq,storage,elasticsearch,vector_store,llm",
    )
    m = sub.add_parser("wait-migrations", help="ждать, пока схема БД дойдёт до версии образа")
    m.add_argument("--timeout", type=float, default=600)
    i = sub.add_parser("init-storage", help="дождаться S3 и создать бакет, если его нет")
    i.add_argument("--timeout", type=float, default=300)
    c = sub.add_parser("cleanup-files", help="удалить файлы с истёкшим сроком хранения")
    c.add_argument("--every", type=float, help="повторять каждые N секунд (без него — один раз)")
    u = sub.add_parser("create-user", help="создать локального пользователя")
    u.add_argument("--login", required=True)
    u.add_argument("--full-name")
    u.add_argument("--roles", default="user", help=f"через запятую: {','.join(ROLES)}")
    u.add_argument("--password-env", help="взять пароль из переменной окружения")
    u.add_argument("--if-missing", action="store_true", help="не ошибка, если уже есть")
    args = p.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)
    if args.cmd == "wait-deps":
        only = set(args.only.split(",")) if args.only else None
        return 0 if wait_for_dependencies(settings, args.timeout, only=only) else 1
    if args.cmd == "wait-migrations":
        return _wait_migrations(settings, args.timeout)
    if args.cmd == "init-storage":
        return _init_storage(args.timeout)
    if args.cmd == "cleanup-files":
        return _cleanup_files(args.every)
    return _create_user(args)


def _wait_migrations(settings: Settings, timeout_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    while not migrations_applied(settings):
        if time.monotonic() > deadline:
            print("Миграции не применены: схема БД старше образа", file=sys.stderr)
            return 1
        time.sleep(5)
    return 0


def _cleanup_files(every_s: float | None) -> int:
    while True:
        try:
            cleanup_expired_files(get_sessionmaker(), get_storage())
        except Exception:
            if every_s is None:
                raise
            logging.getLogger(__name__).exception("Сбой автоудаления, повтор через %s с", every_s)
        if every_s is None:
            return 0
        time.sleep(every_s)


if __name__ == "__main__":
    sys.exit(main())
