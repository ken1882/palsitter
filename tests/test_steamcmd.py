import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from module.steamcmd import ensure_steamcmd_at, validate_steamcmd_runtime


class FakeResponse:
    def __init__(self, data, chunk_size=7):
        self.data = data
        self.chunk_size = chunk_size
        self.offset = 0
        self.headers = {"Content-Length": str(len(data))}
        self.read_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        self.read_calls += 1
        if self.offset >= len(self.data):
            return b""
        size = min(self.chunk_size, size if size >= 0 else self.chunk_size)
        chunk = self.data[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def _archive_bytes():
    output = io.BytesIO()
    if os.name == "nt":
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("steamcmd.exe", b"steamcmd")
    else:
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            data = b"#!/bin/sh\n"
            info = tarfile.TarInfo("steamcmd.sh")
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _steamcmd_path(tmp_path):
    return tmp_path / "steamcmd" / ("steamcmd.exe" if os.name == "nt" else "steamcmd")


def test_steamcmd_download_streams_to_temporary_archive_and_preserves_server_files(tmp_path):
    steamcmd = _steamcmd_path(tmp_path)
    existing = steamcmd.parent / "steamapps" / "common" / "PalServer" / "keep.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("keep", encoding="utf-8")
    response = FakeResponse(_archive_bytes())
    events = []

    result = ensure_steamcmd_at(
        steamcmd,
        opener=lambda *args, **kwargs: response,
        progress=lambda phase, percent, message: events.append((phase, percent, message)),
    )

    assert result == steamcmd
    assert steamcmd.is_file()
    assert existing.read_text(encoding="utf-8") == "keep"
    assert response.read_calls > 1
    assert events[0][:2] == ("download", 0.0)
    assert events[-1][:2] == ("ready", 100.0)
    assert not list(tmp_path.glob(".steamcmd-*-*"))


def test_invalid_steamcmd_archive_cleans_temporary_files_without_touching_install(tmp_path):
    steamcmd = _steamcmd_path(tmp_path)
    existing = steamcmd.parent / "steamapps" / "common" / "PalServer" / "keep.txt"
    existing.parent.mkdir(parents=True)
    existing.write_text("keep", encoding="utf-8")

    with pytest.raises((ValueError, zipfile.BadZipFile, tarfile.TarError)):
        ensure_steamcmd_at(
            steamcmd,
            opener=lambda *args, **kwargs: FakeResponse(b"not an archive"),
        )

    assert not steamcmd.exists()
    assert existing.read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.glob(".steamcmd-*-*"))


def test_linux_steamcmd_runtime_reports_missing_32_bit_loader(tmp_path, monkeypatch):
    steamcmd = tmp_path / "steamcmd" / "steamcmd"
    binary = steamcmd.parent / "linux32" / "steamcmd"
    binary.parent.mkdir(parents=True)
    steamcmd.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.write_bytes(b"elf")
    monkeypatch.setattr("module.steamcmd.WINDOWS", False)
    monkeypatch.setattr("module.steamcmd.LINUX_STEAMCMD_LOADER", tmp_path / "missing-loader")

    with pytest.raises(RuntimeError, match="lib32gcc-s1"):
        validate_steamcmd_runtime(steamcmd)

    (tmp_path / "missing-loader").write_text("loader", encoding="utf-8")

    validate_steamcmd_runtime(steamcmd)
