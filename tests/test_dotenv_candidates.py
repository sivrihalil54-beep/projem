"""config_manager: çoklu .env aday yolu sırası (PROJECT_ROOT, web/, cwd)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_dotenv_candidate_paths_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import config_manager as cm

    fake_root = tmp_path / "proj"
    fake_root.mkdir()
    monkeypatch.setattr(cm, "PROJECT_ROOT", fake_root)

    web_env = fake_root / "web" / ".env"
    cwd_env = tmp_path / "cwdhere" / ".env"

    monkeypatch.delenv("BLS_DOTENV_PATH", raising=False)
    cwd_dir = tmp_path / "cwdhere"
    cwd_dir.mkdir(exist_ok=True)
    monkeypatch.chdir(cwd_dir)

    paths = cm._dotenv_read_candidate_paths()
    assert paths[0] == (fake_root / ".env").resolve()
    assert paths[1] == (fake_root / "web" / ".env").resolve()
    assert paths[2] == (tmp_path / "cwdhere" / ".env").resolve()


def test_bls_dotenv_path_only_single_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import config_manager as cm

    custom = tmp_path / "secret" / ".env"
    custom.parent.mkdir(parents=True)
    monkeypatch.setenv("BLS_DOTENV_PATH", str(custom))

    paths = cm._dotenv_read_candidate_paths()
    assert len(paths) == 1
    assert paths[0] == custom.resolve()
