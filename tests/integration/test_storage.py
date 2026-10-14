"""Один контракт FileStorage для всех трёх бэкендов; s3 — на SeaweedFS в Docker."""

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from gosdoc.core.config import Settings
from gosdoc.core.storage import (
    FileNotFoundInStorage,
    FileStorage,
    LocalStorage,
    PostgresStorage,
    S3Storage,
)
from testcontainers.core.container import DockerContainer

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def seaweedfs() -> Iterator[str]:
    image = os.environ.get("TC_SEAWEEDFS_IMAGE", "chrislusf/seaweedfs:4.46")
    container = (
        DockerContainer(image).with_command("server -s3 -dir=/data").with_exposed_ports(8333)
    )
    with container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8333)}"


def _s3(endpoint: str) -> S3Storage:
    storage = S3Storage(Settings(s3_endpoint_url=endpoint, s3_bucket="test"))
    deadline = time.monotonic() + 60
    while True:  # SeaweedFS поднимает master/volume/filer/s3 не сразу
        try:
            storage.ensure_bucket()
            return storage
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)


@pytest.fixture(params=["local", "postgres", "s3"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> FileStorage:
    if request.param == "local":
        return LocalStorage(str(tmp_path))
    if request.param == "postgres":
        return PostgresStorage(request.getfixturevalue("sessions"))
    return _s3(request.getfixturevalue("seaweedfs"))


def test_contract(storage: FileStorage) -> None:
    prefix = f"t{time.time_ns()}/"
    data = "Привет, мир".encode() * 1000

    storage.put(prefix + "a", data)
    storage.put(prefix + "b", b"1")
    storage.put(prefix + "b", b"2")  # перезапись

    assert storage.get(prefix + "a") == data
    assert storage.get(prefix + "b") == b"2"
    assert storage.list(prefix) == [prefix + "a", prefix + "b"]

    storage.delete(prefix + "a")
    storage.delete(prefix + "a")  # идемпотентно
    with pytest.raises(FileNotFoundInStorage):
        storage.get(prefix + "a")
    assert storage.list(prefix) == [prefix + "b"]
    storage.ping()


def test_s3_presigned_url(seaweedfs: str) -> None:
    storage = _s3(seaweedfs)
    storage.put("p/doc.txt", b"content")
    url = storage.presigned_url("p/doc.txt")
    assert url is not None and "X-Amz-Signature" in url
