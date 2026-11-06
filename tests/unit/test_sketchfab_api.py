from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator import __version__
from onward_generator.overgrow import sketchfab_fetch
from onward_generator.overgrow.sketchfab_fetch import SketchfabError, api_token, fetch_sketchfab

UID = "97e3bdfe47ea40f49351debaa11cd1e2"
SIGNED = "https://sketchfab-prod-media.s3.amazonaws.com/archives/a.zip?X-Amz-Signature=abc"


def test_token_is_read_and_trimmed() -> None:
    assert api_token({"SKETCHFAB_API_TOKEN": "  abc123  "}) == "abc123"


@pytest.mark.parametrize("env", [{}, {"SKETCHFAB_API_TOKEN": ""}, {"SKETCHFAB_API_TOKEN": "   "}])
def test_a_missing_or_blank_token_is_refused(env: dict[str, str]) -> None:
    with pytest.raises(SketchfabError, match="SKETCHFAB_API_TOKEN"):
        api_token(env)


def test_token_defaults_to_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SKETCHFAB_API_TOKEN", "from-env")
    assert api_token() == "from-env"


class _Recorder:
    def __init__(self, listing: dict[str, Any], archive: bytes = b"PK-archive") -> None:
        self.listing = listing
        self.archive = archive
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> bytes:
        self.calls.append({"url": url, **kwargs})
        if url.startswith("https://api.sketchfab.com/"):
            return json.dumps(self.listing).encode()
        return self.archive


def test_download_sends_the_token_to_the_api_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorder = _Recorder({"gltf": {"url": SIGNED, "size": 10, "expires": 300}})
    monkeypatch.setattr(sketchfab_fetch, "fetch_bytes", recorder)
    target = sketchfab_fetch._download(UID, tmp_path / "raw", "tok")
    api, archive = recorder.calls
    assert api["url"] == f"https://api.sketchfab.com/v3/models/{UID}/download"
    assert api["headers"] == {
        "User-Agent": f"onward-generator/{__version__}",
        "Authorization": "Token tok",
        "Accept": "application/json",
    }
    assert api["allowed_hosts"] == frozenset({"api.sketchfab.com"})
    assert api["max_bytes"] == sketchfab_fetch.MAX_JSON_BYTES
    assert api["error"] is SketchfabError
    assert api["timeout"] == 60
    assert archive["url"] == SIGNED
    assert archive["headers"] == {"User-Agent": f"onward-generator/{__version__}"}
    assert archive["allowed_hosts"] == frozenset({"sketchfab-prod-media.s3.amazonaws.com"})
    assert archive["max_bytes"] == sketchfab_fetch.MAX_BYTES
    assert archive["timeout"] == 300
    assert target == tmp_path / "raw" / "download.gltf.bin"
    assert target.read_bytes() == b"PK-archive"


def test_an_archive_over_the_cap_is_not_requested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    size = sketchfab_fetch.MAX_BYTES + 1
    recorder = _Recorder({"glb": {"url": SIGNED, "size": size}, "gltf": {"url": SIGNED, "size": size}})
    monkeypatch.setattr(sketchfab_fetch, "fetch_bytes", recorder)
    with pytest.raises(SketchfabError, match="under the size cap"):
        sketchfab_fetch._download(UID, tmp_path, "tok")
    assert len(recorder.calls) == 1


def test_an_archive_at_the_cap_is_requested(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorder = _Recorder({"gltf": {"url": SIGNED, "size": sketchfab_fetch.MAX_BYTES}})
    monkeypatch.setattr(sketchfab_fetch, "fetch_bytes", recorder)
    sketchfab_fetch._download(UID, tmp_path, "tok")
    assert len(recorder.calls) == 2


def test_a_declared_size_over_the_cap_stops_before_the_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sketchfab_fetch, "_pick", lambda payload: ("gltf", {"url": SIGNED, "size": sketchfab_fetch.MAX_BYTES + 1}))
    recorder = _Recorder({})
    monkeypatch.setattr(sketchfab_fetch, "fetch_bytes", recorder)
    with pytest.raises(SketchfabError, match="over"):
        sketchfab_fetch._download(UID, tmp_path, "tok")
    assert len(recorder.calls) == 1


def test_fetch_without_a_token_makes_no_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"assets": [{"uid": UID, "usable": True}]}), encoding="utf-8")
    monkeypatch.delenv("SKETCHFAB_API_TOKEN", raising=False)
    recorder = _Recorder({})
    monkeypatch.setattr(sketchfab_fetch, "fetch_bytes", recorder)
    with pytest.raises(SketchfabError, match="SKETCHFAB_API_TOKEN"):
        fetch_sketchfab(tmp_path / "raw", tmp_path / "lib", review)
    assert recorder.calls == []


def test_fetch_passes_the_token_to_each_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"assets": [{"uid": UID, "usable": True}]}), encoding="utf-8")
    monkeypatch.setenv("SKETCHFAB_API_TOKEN", "tok")
    monkeypatch.setattr(sketchfab_fetch.time, "sleep", lambda seconds: None)
    seen: list[tuple[Any, ...]] = []

    def one(uid: str, spec: dict, row: dict, raw: Path, library: Path, token: str) -> dict:
        seen.append((uid, spec, row, raw, library, token))
        raise SketchfabError("stop")

    monkeypatch.setattr(sketchfab_fetch, "_one", one)
    fetch_sketchfab(tmp_path / "raw", tmp_path / "lib", review)
    row = {"uid": UID, "usable": True, "installed": False, "id": "mustang_boss_429", "error": "stop"}
    assert seen == [(UID, sketchfab_fetch.SPECS[UID], row, (tmp_path / "raw").resolve(), (tmp_path / "lib").resolve(), "tok")]
