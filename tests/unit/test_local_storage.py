from pathlib import Path

import pytest
from gosdoc.core.storage import LocalStorage


def test_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalStorage(str(tmp_path / "files"))
    with pytest.raises(ValueError, match="Недопустимый ключ"):
        storage.put("../outside", b"x")
