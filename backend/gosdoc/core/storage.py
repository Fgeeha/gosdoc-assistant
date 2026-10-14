"""Хранилище файлов (ADR-0004): s3 / postgres / local, выбор в конфигурации.

Бэкенд хранит только байты по ключу; метаданные — в таблице `files`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from gosdoc.core.config import Settings, get_settings
from gosdoc.core.db import FileBlob, get_sessionmaker


class FileNotFoundInStorage(KeyError):
    pass


class FileStorage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str = "") -> list[str]: ...
    def presigned_url(self, key: str, expires_s: int = 300) -> str | None: ...
    def ping(self) -> None: ...


class S3Storage:
    """Любое S3-совместимое хранилище (SeaweedFS, RustFS, ...): только базовые операции."""

    def __init__(self, s: Settings) -> None:
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
            verify=s.ca_bundle or True,
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get(self, key: str) -> bytes:
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise FileNotFoundInStorage(key) from e
            raise
        return bytes(body.read())

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def list(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            keys += [o["Key"] for o in page.get("Contents", [])]
        return sorted(keys)

    def presigned_url(self, key: str, expires_s: int = 300) -> str | None:
        return str(
            self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_s
            )
        )

    def ping(self) -> None:
        self.client.head_bucket(Bucket=self.bucket)


class PostgresStorage:
    """Запасной вариант без внешнего хранилища: таблица file_blobs."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def put(self, key: str, data: bytes) -> None:
        stmt = insert(FileBlob).values(storage_key=key, data=data)
        stmt = stmt.on_conflict_do_update(index_elements=["storage_key"], set_={"data": data})
        with self.sessions.begin() as db:
            db.execute(stmt)

    def get(self, key: str) -> bytes:
        with self.sessions() as db:
            data = db.scalar(select(FileBlob.data).where(FileBlob.storage_key == key))
        if data is None:
            raise FileNotFoundInStorage(key)
        return bytes(data)

    def delete(self, key: str) -> None:
        with self.sessions.begin() as db:
            db.execute(delete(FileBlob).where(FileBlob.storage_key == key))

    def list(self, prefix: str = "") -> list[str]:
        with self.sessions() as db:
            stmt = select(FileBlob.storage_key).where(FileBlob.storage_key.startswith(prefix))
            return list(db.scalars(stmt.order_by(FileBlob.storage_key)))

    def presigned_url(self, key: str, expires_s: int = 300) -> str | None:
        return None  # содержимое отдаётся через API

    def ping(self) -> None:
        with self.sessions() as db:
            db.execute(select(1))


class LocalStorage:
    """Каталог на локальной ФС или PVC — для отладки."""

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Недопустимый ключ: {key}")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as e:
            raise FileNotFoundInStorage(key) from e

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list(self, prefix: str = "") -> list[str]:
        keys = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix))

    def presigned_url(self, key: str, expires_s: int = 300) -> str | None:
        return None

    def ping(self) -> None:
        if not self.root.is_dir():
            raise OSError(f"Каталог хранилища недоступен: {self.root}")


def make_storage(s: Settings) -> FileStorage:
    if s.storage_backend == "s3":
        return S3Storage(s)
    if s.storage_backend == "postgres":
        return PostgresStorage(get_sessionmaker())
    return LocalStorage(s.storage_local_path)


@lru_cache
def get_storage() -> FileStorage:
    return make_storage(get_settings())
