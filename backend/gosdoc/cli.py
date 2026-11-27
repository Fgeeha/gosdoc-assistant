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
from sqlalchemy import func, select

from gosdoc.core.auth import ROLES, AuthError, check_password_policy, new_local_user, validate_roles
from gosdoc.core.config import Settings, get_settings
from gosdoc.core.db import User, get_sessionmaker
from gosdoc.core.files import cleanup_expired_files
from gosdoc.core.health import migrations_applied, wait_for_dependencies
from gosdoc.core.logging import setup_logging
from gosdoc.core.queue import enqueue, enqueue_after_commit
from gosdoc.core.storage import S3Storage, get_storage
from gosdoc.docs_check.models import NpaEdition
from gosdoc.docs_check.npa import queue_editions


def _create_user(args: argparse.Namespace) -> int:
    settings = get_settings()
    # Существование — до проверки пароля: иначе --if-missing падает на старом пароле из .env.
    with get_sessionmaker()() as db:
        if db.scalar(select(User.id).where(User.login == args.login)):
            print(f"Пользователь {args.login} уже есть")
            return 0 if args.if_missing else 1
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


def _reindex_npa(wait: bool, timeout_s: float) -> int:
    """Полная переиндексация НПА — после подключения Elasticsearch или векторной базы."""
    deadline = time.monotonic() + timeout_s
    sessions = get_sessionmaker()
    with sessions.begin() as db:
        ids = queue_editions(db, full=True)
        enqueue_after_commit(db, enqueue, "npa", "index_npa_edition", ids)
    print(f"В очередь поставлено редакций: {len(ids)}")
    while wait:
        with sessions() as db:
            rows = db.execute(
                select(NpaEdition.status, func.count())
                .where(NpaEdition.id.in_(ids))
                .group_by(NpaEdition.status)
            )
            counts = {status: n for status, n in rows}
        print(", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "нет редакций")
        if not counts.get("queued"):
            return 1 if counts.get("failed") else 0
        if time.monotonic() > deadline:
            # Редакция остаётся queued, если воркер не слушает очередь npa или задача
            # снята по лимиту времени.
            print("Индексация не завершилась за отведённое время", file=sys.stderr)
            return 1
        time.sleep(10)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gosdoc")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wait-deps", help="ждать доступности всех зависимостей")
    w.add_argument("--timeout", type=float, default=300)
    w.add_argument(
        "--only",
        help="через запятую: postgres,redis,rabbitmq,storage,llm, а также elasticsearch "
        "и vector_store, если они настроены",
    )
    m = sub.add_parser("wait-migrations", help="ждать, пока схема БД дойдёт до версии образа")
    m.add_argument("--timeout", type=float, default=600)
    i = sub.add_parser("init-storage", help="дождаться S3 и создать бакет, если его нет")
    i.add_argument("--timeout", type=float, default=300)
    c = sub.add_parser("cleanup-files", help="удалить файлы с истёкшим сроком хранения")
    c.add_argument("--every", type=float, help="повторять каждые N секунд (без него — один раз)")
    r = sub.add_parser("reindex-npa", help="переиндексировать всю нормативную базу")
    r.add_argument("--wait", action="store_true", help="ждать завершения; код 1 при ошибках")
    r.add_argument("--timeout", type=float, default=7200, help="предел ожидания, с")
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
    if args.cmd == "reindex-npa":
        return _reindex_npa(args.wait, args.timeout)
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
