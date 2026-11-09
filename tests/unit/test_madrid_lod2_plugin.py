"""Plan D6: the opt-in ``es.madrid_lod2`` source plugin: licence, coverage, stages, the block query and the download."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from onward_generator.sources import registry
from onward_generator.sources.es import madrid_lod2 as plugin_module
from onward_generator.sources.es.madrid_lod2 import (
    ATTRIBUTION,
    MAX_REPLY_BYTES,
    MAX_ZIP_BYTES,
    MadridLod2Plugin,
    block_query_url,
    candidate_ids,
    describe_blocks,
    fetch_lod2,
    http_fetch,
    parse_block_reply,
    retry_once,
)
from onward_generator.sources.plugin import SourceError
from tests.conftest import MURCIA_CENTER
from tests.lod2_fixture import box_group, obj_text, obj_zip

PLUGIN = MadridLod2Plugin()
MADRID = (-3.70350, 40.41690)
PARIS = (2.34830, 48.85300)
KRAKOW = (19.93660, 50.06140)
OBJ_URL = "https://geoportal.madrid.es/fsdescargas/IDEAM_WBGEOPORTAL/CARTOGRAFIA/M-000214.zip"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def reply(*rows: tuple[str, str]) -> bytes:
    return json.dumps({"features": [{"attributes": {"COD_MANZAN": code, "URL_OBJ": url}} for code, url in rows]}).encode()


def test_metadata_licence_and_coverage() -> None:
    assert (PLUGIN.id, PLUGIN.opt_in, PLUGIN.produces) == ("es.madrid_lod2", True, frozenset({"landmark_mesh"}))
    assert PLUGIN.licence.spdx == "CC-BY-4.0" and PLUGIN.licence.attribution == ATTRIBUTION
    assert ATTRIBUTION.startswith("Origen de los datos: Ayuntamiento de Madrid") and "2026-07-23" in ATTRIBUTION
    registry.check_licence(PLUGIN, registry.load_source_config())
    assert PLUGIN.covers(*MADRID)
    assert not PLUGIN.covers(*MURCIA_CENTER) and not PLUGIN.covers(*PARIS) and not PLUGIN.covers(*KRAKOW)
    assert registry.plugin_by_id("es.madrid_lod2") == PLUGIN


def test_steps_are_one_download_that_hands_detail_the_index(tmp_path: Path) -> None:
    scene = tmp_path / "data" / "processed" / "cadastre" / "geo_scene.json"
    steps = PLUGIN.steps(tmp_path, scene)
    index = tmp_path / "data" / "raw" / "madrid_lod2" / "lod2_index.json"
    assert len(steps.stages) == 1
    stage = steps.stages[0]
    assert (stage.name, stage.marker, stage.inputs, stage.download) == ("madrid_lod2", index, (scene,), True)
    assert list(stage.argv[stage.argv.index("source"):]) == ["source", "madrid-lod2", "--scene", str(scene), "--out", str(index.parent)]
    assert steps.landmark_meshes == index
    assert (steps.overrides, steps.scene, steps.secondary_overrides, steps.places, steps.tints, steps.roof_spread) == (None,) * 6


def test_block_query_url_is_exact() -> None:
    assert block_query_url((-3.7045, 40.416, -3.7025, 40.4175)) == (
        "https://services-eu1.arcgis.com/UrtTdLP2PYSxwtI6/arcgis/rest/services/MANZANAS_PARA_DESCARGA/FeatureServer/0/query?"
        "where=1%3D1&geometry=-3.7045000%2C40.4160000%2C-3.7025000%2C40.4175000&geometryType=esriGeometryEnvelope&inSR=4326"
        "&spatialRel=esriSpatialRelIntersects&outFields=COD_MANZAN%2CURL_OBJ&returnGeometry=false&orderByFields=COD_MANZAN&f=json"
    )


def test_parse_block_reply_sorts_by_code() -> None:
    body = reply(("M-000483", OBJ_URL + "?b"), ("M-000214", OBJ_URL))
    assert list(parse_block_reply(body).items()) == [("M-000214", OBJ_URL), ("M-000483", OBJ_URL + "?b")]
    assert parse_block_reply(reply()) == {}
    assert parse_block_reply(reply(("M-1", OBJ_URL), ("M-2", OBJ_URL)), max_blocks=2) == {"M-1": OBJ_URL, "M-2": OBJ_URL}


@pytest.mark.parametrize(("body", "message"), [
    (reply(("M-1", "https://evil.example/M-1.zip")), "block M-1 points at 'evil.example', not geoportal.madrid.es"),
    (reply(("M-1", "http://geoportal.madrid.es/M-1.zip")), "block M-1 points at 'geoportal.madrid.es', not geoportal.madrid.es"),
    (json.dumps({"features": [{"attributes": {"COD_MANZAN": "M-1"}}]}).encode(), "a block needs COD_MANZAN and URL_OBJ"),
    (json.dumps({"features": [{"attributes": {"URL_OBJ": OBJ_URL}}]}).encode(), "a block needs COD_MANZAN and URL_OBJ"),
    (json.dumps({"features": [{"attributes": {"COD_MANZAN": "", "URL_OBJ": OBJ_URL}}]}).encode(), "a block needs COD_MANZAN and URL_OBJ"),
    (json.dumps({"features": ["row"]}).encode(), "a block needs COD_MANZAN and URL_OBJ"),
    (reply(("../M-1", OBJ_URL)), "block code '../M-1' is not a plain code"),
    (json.dumps({"error": {"code": 400, "message": "Invalid query"}}).encode(), "the block query failed"),
    (json.dumps([1, 2]).encode(), "the block query failed: None"),
    (json.dumps({"features": {}}).encode(), "the block query reply has no features list"),
    (b"<html>servicio no disponible</html>", "the block query did not return JSON"),
    (reply(("M-1", OBJ_URL), ("M-2", OBJ_URL), ("M-3", OBJ_URL)), "3 blocks is over the limit of 2"),
])
def test_parse_block_reply_refuses(body: bytes, message: str) -> None:
    with pytest.raises(SourceError, match=message.replace("(", r"\(").replace(".", r"\.")):
        parse_block_reply(body, max_blocks=2)


def test_candidates_are_the_heights_landmarks(detail_inputs: tuple[Path, Path, Path]) -> None:
    scene = json.loads(detail_inputs[0].read_bytes())
    assert candidate_ids(scene) == ["osm-way-314"]  # the church; the shop, houses and shed are not landmarks
    assert candidate_ids({"features": [{"kind": "poi", "id": "x", "tags": {"building": "church"}}]}) == []
    assert candidate_ids({"features": [{"kind": "building", "id": "b", "tags": {"historic": "yes"}},
                                       {"kind": "building", "id": "a", "tags": {"amenity": "place_of_worship"}}]}) == ["a", "b"]


class FakeFetch:
    """The FeatureServer and the OBJ host: a reply per URL prefix, ``fail`` failures before each success."""

    def __init__(self, zip_body: bytes, *, fail: int = 0, fail_on: str = "") -> None:
        self.zip_body = zip_body
        self.fail = fail
        self.fail_on = fail_on
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, max_bytes: int) -> bytes:
        self.calls.append((url, max_bytes))
        if self.fail and self.fail_on in url:
            self.fail -= 1
            raise SourceError(f"request to {url} failed")
        if "FeatureServer" in url:
            return reply(("M-000214", OBJ_URL))
        return self.zip_body


def _zip() -> bytes:
    return obj_zip(obj_text([("g", box_group(30, 6, 50, 30, 15.0))], (0.0, 0.0)))


def test_fetch_writes_the_zips_the_index_and_the_sidecar(tmp_path: Path, detail_inputs: tuple[Path, Path, Path]) -> None:
    body = _zip()
    fetch = FakeFetch(body)
    out = tmp_path / "raw" / "madrid_lod2"
    index = fetch_lod2(detail_inputs[0], out, fetch=fetch, now=NOW)
    assert [(url.split("?")[0].rsplit("/", 1)[-1], cap) for url, cap in fetch.calls] == [("query", MAX_REPLY_BYTES), ("M-000214.zip", MAX_ZIP_BYTES)]
    assert (out / "M-000214.zip").read_bytes() == body
    assert json.loads((out / "lod2_index.json").read_bytes()) == index
    assert index["blocks"] == [{"cod_manzan": "M-000214", "path": "M-000214.zip", "sha256": hashlib.sha256(body).hexdigest(),
                                "size_bytes": len(body), "url": OBJ_URL}]
    assert {k: index[k] for k in ("schema", "schema_version", "source", "license", "attribution", "data_date", "crs", "candidates")} == {
        "schema": "onward_generator.lod2_landmarks", "schema_version": "1.0.0", "source": "es.madrid_lod2", "license": "CC-BY-4.0",
        "attribution": ATTRIBUTION, "data_date": "2026-07-23", "crs": "EPSG:25830", "candidates": ["osm-way-314"]}
    assert index["scene"]["sha256"] == hashlib.sha256(detail_inputs[0].read_bytes()).hexdigest()
    side = json.loads((tmp_path / "raw" / "madrid_lod2.source.json").read_bytes())
    assert (side["attribution"], side["data_date"], side["retrieved_at"], side["license"]) == (
        ATTRIBUTION, "2026-07-23", "2026-11-09T12:00:00+00:00", "CC-BY-4.0")
    assert side["blocks"] == index["blocks"] and side["service"].endswith("MANZANAS_PARA_DESCARGA/FeatureServer/0")
    assert side["license_url"] == "https://creativecommons.org/licenses/by/4.0/" and "indicate the changes" in side["obligations"]
    assert describe_blocks(index) == "1 block(s) for 1 landmark candidate(s)"
    with pytest.raises(FileExistsError):
        fetch_lod2(detail_inputs[0], out, fetch=fetch, now=NOW)
    again = fetch_lod2(detail_inputs[0], out, fetch=FakeFetch(body), now=NOW, overwrite=True)
    assert again == index and sorted(p.name for p in out.iterdir()) == ["M-000214.zip", "lod2_index.json"]


@pytest.mark.parametrize("fail_on", ["FeatureServer", "geoportal"])
def test_a_failed_download_is_retried_exactly_once(tmp_path: Path, detail_inputs: tuple[Path, Path, Path], fail_on: str) -> None:
    fetch = FakeFetch(_zip(), fail=1, fail_on=fail_on)
    fetch_lod2(detail_inputs[0], tmp_path / "madrid_lod2", fetch=fetch, now=NOW)
    assert len(fetch.calls) == 3
    assert sum(fail_on in url for url, _ in fetch.calls) == 2


@pytest.mark.parametrize("fail_on", ["FeatureServer", "geoportal"])
def test_a_second_failure_leaves_no_index(tmp_path: Path, detail_inputs: tuple[Path, Path, Path], fail_on: str) -> None:
    fetch = FakeFetch(_zip(), fail=2, fail_on=fail_on)
    with pytest.raises(SourceError, match="failed"):
        fetch_lod2(detail_inputs[0], tmp_path / "madrid_lod2", fetch=fetch, now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_a_body_that_is_not_a_zip_is_refused(tmp_path: Path, detail_inputs: tuple[Path, Path, Path]) -> None:
    with pytest.raises(SourceError, match="block M-000214 is not a ZIP file"):
        fetch_lod2(detail_inputs[0], tmp_path / "madrid_lod2", fetch=FakeFetch(b"<html>servicio no disponible</html>"), now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_too_many_blocks_and_another_frame_are_refused(tmp_path: Path, detail_inputs: tuple[Path, Path, Path]) -> None:
    with pytest.raises(SourceError, match="1 blocks is over the limit of 0"):
        fetch_lod2(detail_inputs[0], tmp_path / "madrid_lod2", fetch=FakeFetch(_zip()), now=NOW, max_blocks=0)
    scene = json.loads(detail_inputs[0].read_bytes())
    scene["frame"]["crs"] = "EPSG:32630"
    other = tmp_path / "geo_scene.json"
    other.write_text(json.dumps(scene), encoding="utf-8")
    fetch = FakeFetch(_zip())
    with pytest.raises(SourceError, match="the scene frame must be EPSG:25830"):
        fetch_lod2(other, tmp_path / "madrid_lod2", fetch=fetch, now=NOW)
    assert fetch.calls == []


def test_a_scene_without_landmarks_writes_an_empty_index_without_any_request(tmp_path: Path, row_inputs: tuple[Path, Path, Path]) -> None:
    fetch = FakeFetch(_zip())
    index = fetch_lod2(row_inputs[0], tmp_path / "madrid_lod2", fetch=fetch, now=NOW)
    assert (index["blocks"], index["candidates"], fetch.calls) == ([], [], [])


def test_retry_once_and_the_http_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def flaky(url: str, max_bytes: int) -> bytes:
        calls.append(url)
        raise SourceError("down")

    with pytest.raises(SourceError, match="down"):
        retry_once(flaky, "https://x", 1)
    assert calls == ["https://x", "https://x"]
    seen: dict[str, Any] = {}

    def fake_fetch_bytes(url: str, **kwargs: Any) -> bytes:
        seen.update(kwargs, url=url)
        return b"body"

    monkeypatch.setattr(plugin_module, "fetch_bytes", fake_fetch_bytes)
    assert http_fetch(timeout=5.0)(OBJ_URL, 123) == b"body"
    assert seen["url"] == OBJ_URL and seen["max_bytes"] == 123 and seen["timeout"] == 5.0
    assert seen["allowed_hosts"] == frozenset({"services-eu1.arcgis.com", "geoportal.madrid.es"})
    assert seen["error"] is SourceError and seen["headers"]["User-Agent"].startswith("onward-generator/")
    assert "retry_transient" not in seen and "retry_429" not in seen


def test_the_real_fetch_refuses_an_oversized_body() -> None:
    from onward_generator import net

    class Big:
        def __init__(self) -> None:
            self.left = 3

        def read(self, size: int = -1) -> bytes:
            self.left -= 1
            return b"x" * 10 if self.left >= 0 else b""

        def __enter__(self) -> Big:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    with pytest.raises(SourceError, match="larger than 20 bytes"):
        net.fetch_bytes(OBJ_URL, allowed_hosts=frozenset({"geoportal.madrid.es"}), max_bytes=20, timeout=1.0, headers={},
                        error=SourceError, opener=lambda request, timeout: Big())


# ---------------------------------------------------------------------------------------------------- edges


def test_a_failed_query_quotes_the_service_error() -> None:
    body = json.dumps({"error": {"code": 400, "message": "Invalid query"}}).encode()
    with pytest.raises(SourceError, match=r"^es\.madrid_lod2: the block query failed: \{'code': 400, 'message': 'Invalid query'\}$"):
        parse_block_reply(body)


@pytest.mark.parametrize(("attributes", "message"), [
    ({"COD_MANZAN": "M-1", "URL_OBJ": 5}, "a block needs COD_MANZAN and URL_OBJ"),
    ({"COD_MANZAN": "-", "URL_OBJ": OBJ_URL}, "block code '-' is not a plain code"),
    ({"COD_MANZAN": "--", "URL_OBJ": OBJ_URL}, "block code '--' is not a plain code"),
])
def test_a_block_row_needs_a_text_url_and_a_code_with_letters_or_digits(attributes: dict[str, Any], message: str) -> None:
    with pytest.raises(SourceError, match=message):
        parse_block_reply(json.dumps({"features": [{"attributes": attributes}]}).encode())


def test_candidates_of_a_scene_without_features_and_after_a_non_building() -> None:
    assert candidate_ids({}) == []
    features = [{"kind": "poi", "id": "p", "tags": {}}, {"kind": "building", "id": "b", "tags": {"historic": "yes"}}]
    assert candidate_ids({"features": features}) == ["b"]


def _two_landmarks(tmp_path: Path, scene_path: Path) -> Path:
    """The fixture scene with a second church on the first one's footprint: two candidates, two block queries."""
    scene = json.loads(scene_path.read_bytes())
    church = next(f for f in scene["features"] if f["id"] == "osm-way-314")
    scene["features"].append({**church, "id": "osm-way-999"})
    path = tmp_path / "two" / "geo_scene.json"
    path.parent.mkdir()
    path.write_text(json.dumps(scene), encoding="utf-8")
    return path


class CountingFetch(FakeFetch):
    """Each block query answers a new block code."""

    def __call__(self, url: str, max_bytes: int) -> bytes:
        self.calls.append((url, max_bytes))
        if "FeatureServer" in url:
            n = sum("FeatureServer" in u for u, _ in self.calls)
            return reply((f"M-00000{n}", OBJ_URL.replace("M-000214", f"M-00000{n}")))
        return self.zip_body


def test_the_union_of_the_replies_is_held_to_the_limit(tmp_path: Path, detail_inputs: tuple[Path, Path, Path]) -> None:
    scene = _two_landmarks(tmp_path, detail_inputs[0])
    assert candidate_ids(json.loads(scene.read_bytes())) == ["osm-way-314", "osm-way-999"]
    with pytest.raises(SourceError, match=r"^es\.madrid_lod2: 2 blocks is over the limit of 1$"):
        fetch_lod2(scene, tmp_path / "over", fetch=CountingFetch(_zip()), now=NOW, max_blocks=1)
    index = fetch_lod2(scene, tmp_path / "at", fetch=CountingFetch(_zip()), now=NOW, max_blocks=2)
    assert [b["cod_manzan"] for b in index["blocks"]] == ["M-000001", "M-000002"]


def test_a_reply_may_hold_more_than_the_default_limit_when_the_map_allows_it(tmp_path: Path, detail_inputs: tuple[Path, Path, Path]) -> None:
    rows = [(f"M-{k:06d}", OBJ_URL.replace("M-000214", f"M-{k:06d}")) for k in range(41)]

    class Many(FakeFetch):
        def __call__(self, url: str, max_bytes: int) -> bytes:
            self.calls.append((url, max_bytes))
            return reply(*rows) if "FeatureServer" in url else self.zip_body

    index = fetch_lod2(detail_inputs[0], tmp_path / "many", fetch=Many(_zip()), now=NOW, max_blocks=41)
    assert len(index["blocks"]) == 41


def test_a_sidecar_alone_blocks_the_download(tmp_path: Path, detail_inputs: tuple[Path, Path, Path]) -> None:
    out = tmp_path / "madrid_lod2"
    (tmp_path / "madrid_lod2.source.json").write_text("{}", encoding="utf-8")
    fetch = FakeFetch(_zip())
    with pytest.raises(FileExistsError, match=f"^refusing to overwrite {out}$"):
        fetch_lod2(detail_inputs[0], out, fetch=fetch, now=NOW)
    assert fetch.calls == []


def test_the_download_dates_itself_in_utc_and_makes_its_folders(tmp_path: Path, detail_inputs: tuple[Path, Path, Path]) -> None:
    out = tmp_path / "data" / "raw" / "madrid_lod2"
    index = fetch_lod2(detail_inputs[0], out, fetch=FakeFetch(_zip()))
    side = json.loads((out.parent / "madrid_lod2.source.json").read_bytes())
    assert side["retrieved_at"].endswith("+00:00")
    assert side == {
        "source": "es.madrid_lod2",
        "provider": plugin_module.PROVIDER,
        "license": "CC-BY-4.0",
        "license_name": "Creative Commons Attribution 4.0 International (Ayuntamiento de Madrid)",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": ATTRIBUTION,
        "obligations": plugin_module.LICENCE.obligations,
        "data_date": "2026-07-23",
        "crs": "EPSG:25830",
        "scene": index["scene"],
        "retrieved_at": side["retrieved_at"],
        "user_agent": plugin_module.USER_AGENT,
        "service": plugin_module.FEATURE_SERVER,
        "blocks": index["blocks"],
    }
    assert index["provider"] == plugin_module.PROVIDER and plugin_module.PROVIDER.startswith("Ayuntamiento de Madrid")


def test_a_failure_while_writing_removes_the_staging_folder(tmp_path: Path, detail_inputs: tuple[Path, Path, Path],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    removed: list[tuple[Path, dict[str, Any]]] = []
    real_rmtree = plugin_module.shutil.rmtree

    def rmtree(path: Path, **kwargs: Any) -> None:
        removed.append((Path(path), kwargs))
        real_rmtree(path, **kwargs)

    def broken(path: Path, document: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(plugin_module.shutil, "rmtree", rmtree)
    monkeypatch.setattr(plugin_module, "atomic_write_json", broken)
    with pytest.raises(OSError, match="disk full"):
        fetch_lod2(detail_inputs[0], tmp_path / "madrid_lod2", fetch=FakeFetch(_zip()), now=NOW)
    assert list(tmp_path.iterdir()) == []
    assert [(p.name.startswith(".madrid_lod2."), kwargs) for p, kwargs in removed] == [(True, {"ignore_errors": True})]


def test_the_retry_asks_for_the_same_cap(tmp_path: Path, detail_inputs: tuple[Path, Path, Path]) -> None:
    fetch = FakeFetch(_zip(), fail=1, fail_on="geoportal")
    fetch_lod2(detail_inputs[0], tmp_path / "madrid_lod2", fetch=fetch, now=NOW)
    assert [cap for url, cap in fetch.calls if "geoportal" in url] == [MAX_ZIP_BYTES, MAX_ZIP_BYTES]


def test_the_http_fetch_waits_two_minutes_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(plugin_module, "fetch_bytes", lambda url, **kwargs: seen.update(kwargs) or b"")
    http_fetch()(OBJ_URL, 1)
    assert seen["timeout"] == 120.0


def test_the_source_command_runs_the_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from onward_generator.sources import __main__ as source_cli

    calls: list[tuple[Path, Path, bool]] = []

    def fake(scene: Path, out: Path, *, overwrite: bool) -> dict[str, Any]:
        calls.append((scene, out, overwrite))
        return {"blocks": [{}], "candidates": [{}, {}], "license": "CC-BY-4.0"}

    monkeypatch.setattr(source_cli, "fetch_lod2", fake)
    scene, out = tmp_path / "geo_scene.json", tmp_path / "raw" / "madrid_lod2"
    assert source_cli.main(["madrid-lod2", "--scene", str(scene), "--out", str(out)]) == 0
    assert source_cli.main(["madrid-lod2", "--scene", str(scene), "--out", str(out), "--overwrite"]) == 0
    assert calls == [(scene, out, False), (scene, out, True)]
    printed = capsys.readouterr().out.splitlines()
    assert printed[:2] == ["1 block(s) for 2 landmark candidate(s); licence CC-BY-4.0", f"wrote {out} and its .source.json sidecar"]
    assert source_cli.main(["madrid-lod2", "--out", str(out)]) == 2
    assert source_cli.main(["madrid-lod2", "--scene", str(scene)]) == 2
    assert len(calls) == 2
