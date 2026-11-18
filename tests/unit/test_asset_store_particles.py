"""Asset Store particle packages (ADR-035 amendment of 18 November 2026): the named textures, with alpha and flipbook grid."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from PIL import Image

from onward_generator.assetstore.packages import MAX_MEMBER_BYTES, find_packages
from onward_generator.assetstore.particles import (
    ALPHA_MODES,
    PARTICLES_SCHEMA,
    ROLES,
    SCHEMA_PATH,
    TABLE_PATH,
    ParticleSpec,
    ParticleTableError,
    local_particles,
    luminance_alpha,
    parse_table,
    particle_rows,
    read_table,
)

FLAME = "Assets/Pack/Fire/Flame.tif"
MOTE = "Assets/Pack/Misc/Mote.png"


def _image(size: tuple[int, int], fmt: str = "PNG", mode: str = "RGBA") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, (250, 140, 20, 90)[:len(mode)]).save(out, format=fmt)
    return out.getvalue()


def _package(tmp_path: Path, members: dict[str, bytes], prefix: str = "") -> Path:
    path = tmp_path / "cache" / "Unity Technologies" / "Particle Systems" / "Particle Pack.unitypackage"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for k, (asset_path, data) in enumerate(members.items()):
            for name, payload in (("pathname", f"{asset_path}\n00".encode()), ("asset", data)):
                info = tarfile.TarInfo(f"{prefix}{k:032x}/{name}")
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return path


SPECS = {
    "dust_mote": ParticleSpec("Assets/Pack/Missing.png", (5, 5), 1024, "keep", "DustMotesEffect", "Not in the package."),
    "flame": ParticleSpec(FLAME, (8, 4), 64, "keep", "MediumFlames", "Flames."),
    "spark": ParticleSpec(MOTE, (1, 1), 256, "keep", "ElectricalSparks", "A spark."),
}
"""In role order the missing image comes first, so a role left out does not end the loop."""


def test_the_table_names_the_starter_pack_s_textures() -> None:
    table = read_table()
    assert TABLE_PATH.name == "asset_store_particles.json" and sorted(table) == ["particle_pack_starter_assets"]
    pack = table["particle_pack_starter_assets"]
    assert sorted(pack) == sorted(ROLES)
    assert pack["flame"].tiles == (10, 10) and pack["flame"].max_px == 2048 and pack["flame"].prefab == "MediumFlames"
    assert pack["flame"].path.endswith("/Fire & Explosion Effects/Textures/MediumFlame01.tif")
    assert pack["smoke"].tiles == (5, 5) and pack["dust_mote"].tiles == (1, 1)
    assert {role for role, spec in pack.items() if spec.alpha == "luminance"} == {"flame", "large_flame", "spark"}  # drawn on black
    assert "ground_fog" not in ROLES  # its TIFF alpha does not survive the copy


def test_a_broken_table_names_the_first_problem() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    doc = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    bad = copy.deepcopy(doc)
    bad["packages"]["particle_pack_starter_assets"]["flame"]["tiles"] = [0, 10]
    with pytest.raises(ParticleTableError, match=r"^here: /packages/particle_pack_starter_assets/flame/tiles/0: 0 is less than"):
        parse_table(bad, schema, "here")
    bad = copy.deepcopy(doc)
    bad["packages"]["particle_pack_starter_assets"]["rain"] = bad["packages"]["particle_pack_starter_assets"]["flame"]
    with pytest.raises(ParticleTableError, match="rain"):
        parse_table(bad, schema, "here")
    parsed = parse_table(doc, schema, "here")
    wide = copy.deepcopy(doc)
    wide["packages"]["particle_pack_starter_assets"]["flame"]["tiles"] = [8, 4]
    assert parse_table(wide, schema, "here")["particle_pack_starter_assets"]["flame"].tiles == (8, 4)
    assert parsed["particle_pack_starter_assets"]["spark"] == ParticleSpec(
        "Assets/UnityTechnologies/ParticlePack/EffectExamples/Misc Effects/Textures/SparkParticle.tif", (1, 1), 256,
        "luminance", "ElectricalSparks", "One bright spark.")
    bad = copy.deepcopy(doc)
    bad["packages"]["particle_pack_starter_assets"]["flame"]["alpha"] = "premultiplied"
    with pytest.raises(ParticleTableError, match="/packages/particle_pack_starter_assets/flame/alpha"):
        parse_table(bad, schema, "here")


def test_the_rows_keep_alpha_cap_the_size_and_record_the_flipbook(tmp_path: Path) -> None:
    _package(tmp_path, {FLAME: _image((256, 128), "TIFF"), MOTE: _image((32, 32), mode="RGB"), "Assets/Pack/Readme.txt": b"hello"},
             prefix="./")
    package = find_packages(tmp_path / "cache")[0]
    assets = tmp_path / "assets"
    rows = particle_rows(package, assets, SPECS)
    assert [row["role"] for row in rows] == ["flame", "spark"]  # sorted, the missing mote left out
    flame = rows[0]
    assert flame["path"] == "assets/asset_store_particle_pack/particle_flame.png"
    assert flame["px"] == 64 and flame["tiles"] == [8, 4] and flame["frames"] == 32 and flame["prefab"] == "MediumFlames"
    assert flame["alpha"] == "keep"
    assert flame["license"] == "LicenseRef-Unity-Asset-Store-EULA" and flame["license_url"] == "https://unity.com/legal/as-terms"
    assert flame["credit"] == ("Flame.tif from Particle Pack by Unity Technologies, Unity Asset Store EULA; local copy, never "
                               "committed.") and flame["note"] == "Flames."
    data = (tmp_path / flame["path"]).read_bytes()
    assert flame["sha256"] == hashlib.sha256(data).hexdigest() and flame["bytes"] == len(data)
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG" and image.mode == "RGBA" and image.size == (64, 32)
        red, green, blue, alpha = image.getpixel((5, 5))
        assert alpha == 90 and abs(red - 250) <= 2 and abs(green - 140) <= 3 and abs(blue - 20) <= 2  # resampled
    assert rows[1]["px"] == 32 and rows[1]["frames"] == 1 and rows[1]["path"].endswith("/particle_spark.png")
    with Image.open(assets.parent / rows[1]["path"]) as image:
        assert image.mode == "RGBA" and image.getpixel((0, 0)) == (250, 140, 20, 255)  # an image without alpha gains an opaque one
    manifest = json.loads((assets / "asset_store_particle_pack" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == PARTICLES_SCHEMA and manifest["schema_version"] == "0.1.0" and manifest["particles"] == rows
    assert manifest["source"] == ("Unity Asset Store EULA (Extension Asset), local only: Unity Technologies / Particle Pack. "
                                  "Converted on this machine; never committed or redistributed outside a built map.")
    assert manifest["package"] == {"publisher": "Unity Technologies", "category": "Particle Systems", "name": "Particle Pack"}
    licence = (assets / "asset_store_particle_pack" / "License.txt").read_text(encoding="utf-8")
    assert licence == ("Particle Pack by Unity Technologies, from the Unity Asset Store, under the Standard Unity Asset Store EULA.\n"
                       "Local copy for this user's maps only. Do not commit, share or publish these files.\n")


def test_an_unreadable_or_absent_image_writes_nothing(tmp_path: Path) -> None:
    _package(tmp_path, {FLAME: b"not an image"})
    assets = tmp_path / "assets"
    assert particle_rows(find_packages(tmp_path / "cache")[0], assets, SPECS) == []
    assert not (assets / "asset_store_particle_pack").exists()


def test_an_unreadable_image_does_not_stop_the_next_role(tmp_path: Path) -> None:
    _package(tmp_path, {FLAME: b"not an image", MOTE: _image((16, 16))})
    rows = particle_rows(find_packages(tmp_path / "cache")[0], tmp_path / "assets", SPECS)
    assert [row["role"] for row in rows] == ["spark"]


def test_a_member_over_the_size_cap_is_not_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _package(tmp_path, {MOTE: _image((32, 32))})
    size = len(_image((32, 32)))
    monkeypatch.setattr("onward_generator.assetstore.particles.MAX_MEMBER_BYTES", size - 1)
    assert particle_rows(find_packages(tmp_path / "cache")[0], tmp_path / "assets", SPECS) == []
    monkeypatch.setattr("onward_generator.assetstore.particles.MAX_MEMBER_BYTES", size)
    assert len(particle_rows(find_packages(tmp_path / "cache")[0], tmp_path / "assets", SPECS)) == 1
    assert MAX_MEMBER_BYTES == 512 * 1024 * 1024


def _catalogue(assets: Path, name: str, rows: list[dict[str, object]], schema: str = PARTICLES_SCHEMA,
               source: str = "Unity Asset Store EULA (Extension Asset), local only") -> None:
    folder = assets / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps({"schema": schema, "source": source, "particles": rows}), encoding="utf-8")


def _file(assets: Path, rel: str, data: bytes) -> dict[str, object]:
    path = assets.parent / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": rel, "sha256": hashlib.sha256(data).hexdigest()}


def test_the_local_particles_check_each_file_and_honour_the_switch(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    flame = {"role": "flame", **_file(assets, "assets/asset_store_a/flame.png", b"flame-a"), "tiles": [10, 10]}
    stale = {"role": "smoke", "path": "assets/asset_store_a/smoke.png", "sha256": "0" * 64}
    _file(assets, "assets/asset_store_a/smoke.png", b"changed")
    unknown = {"role": "rain", **_file(assets, "assets/asset_store_a/rain.png", b"rain")}
    outside = {"role": "spark", **_file(assets, "elsewhere/spark.png", b"spark")}
    _catalogue(assets, "asset_store_0", [{"role": "sand_puff", **_file(assets, "assets/asset_store_0/p.png", b"p")}],
               schema="onward_generator.imported_library")  # first in name order: a skipped catalogue does not end the search
    _catalogue(assets, "asset_store_a", [flame, stale, unknown, outside, "x", {"path": "no role"}, {"role": "large_flame"},
                                         {"role": "ground_fog", "path": 3}])
    later = {"role": "flame", **_file(assets, "assets/asset_store_b/flame.png", b"flame-b")}
    mote = {"role": "dust_mote", **_file(assets, "assets/asset_store_b/mote.png", b"mote")}
    _catalogue(assets, "asset_store_b", [later, mote])
    _catalogue(assets, "asset_store_c", [{"role": "spark", **_file(assets, "assets/asset_store_c/s.png", b"s")}],
               schema="onward_generator.imported_library")
    _catalogue(assets, "asset_store_d", [{"role": "ground_fog", **_file(assets, "assets/asset_store_d/f.png", b"f")}],
               source="CC0")
    found = local_particles(assets, env={})
    assert sorted(found) == ["dust_mote", "flame"]
    assert found["flame"]["file"] == (assets / "asset_store_a" / "flame.png").resolve() and found["flame"]["tiles"] == [10, 10]
    assert found["dust_mote"]["file"].read_bytes() == b"mote"
    assert local_particles(assets, env={"ONWARD_LOCAL_CATALOGUES": "0"}) == {}
    assert local_particles(tmp_path / "none", env={}) == {}


def test_the_particles_command_imports_the_table_s_packages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    import onward_generator.assetstore.__main__ as cli

    _package(tmp_path, {FLAME: _image((32, 32)), MOTE: _image((16, 16))})
    second = tmp_path / "cache" / "Pub" / "Particle Systems" / "Sparks.unitypackage"
    second.parent.mkdir(parents=True)
    second.write_bytes(_package(tmp_path / "s", {MOTE: _image((16, 16))}).read_bytes())
    monkeypatch.setattr("onward_generator.assetstore.particles.read_table",
                        lambda: {"particle_pack": SPECS, "absent_pack": {"spark": SPECS["spark"]}, "sparks": {"spark": SPECS["spark"]}})
    monkeypatch.setattr(cli, "ASSETS", tmp_path / "assets")
    assert cli.main(["particles", "--cache", str(tmp_path / "cache")]) == 0
    out = capsys.readouterr().out
    assert "absent_pack: not downloaded\n" in out
    assert "particle_pack: 2 particle textures of 3 named (flame, spark)\n" in out and "sparks: 1 particle textures of 1 named (spark)\n" in out
    assert out.endswith(f"\n3 particle textures under {tmp_path / 'assets'}/asset_store_*\n")  # both packages counted
    assert cli.main(["particles", "--cache", str(tmp_path / "cache"), "--only", "absent_pack"]) == 0
    assert capsys.readouterr().out == f"absent_pack: not downloaded\n0 particle textures under {tmp_path / 'assets'}/asset_store_*\n"
    with pytest.raises(SystemExit, match="asset-store: other, zzz has no particle textures in config/dress/asset_store_particles.json"):
        cli.main(["particles", "--cache", str(tmp_path / "cache"), "--only", "zzz", "other"])


def test_the_particles_command_refuses_a_broken_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    import onward_generator.assetstore.__main__ as cli

    def broken() -> dict[str, dict[str, ParticleSpec]]:
        raise ParticleTableError("table: /packages: bad")

    monkeypatch.setattr("onward_generator.assetstore.particles.read_table", broken)
    assert cli.main(["particles", "--cache", str(tmp_path)]) == 2
    assert capsys.readouterr().err == "asset-store: table: /packages: bad\n"


def test_the_particles_help_documents_its_options(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import onward_generator.assetstore.__main__ as cli

    monkeypatch.setenv("COLUMNS", "400")
    with pytest.raises(SystemExit) as exc:
        cli.main(["particles", "--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    assert text.startswith("usage: onward-generator asset-store particles ")
    assert "the Unity Asset Store cache (default: the host profile's asset_store_cache)" in text and "package ids of the table" in text


def test_an_image_drawn_on_black_takes_its_alpha_from_its_brightness() -> None:
    assert ALPHA_MODES == ("keep", "luminance")
    source = Image.new("RGB", (4, 1))
    source.putpixel((0, 0), (0, 0, 0))
    source.putpixel((1, 0), (200, 100, 50))
    source.putpixel((2, 0), (255, 255, 255))
    source.putpixel((3, 0), (1, 0, 0))
    image = luminance_alpha(source)
    assert image.mode == "RGBA"
    assert [image.getpixel((x, 0)) for x in range(4)] == [(0, 0, 0, 0), (255, 128, 64, 200), (255, 255, 255, 255), (255, 0, 0, 1)]
    grey = luminance_alpha(Image.new("L", (1, 1), 100))  # a single-channel image is read as RGB
    assert grey.getpixel((0, 0)) == (255, 255, 255, 100)


def test_a_luminance_role_is_written_without_its_black(tmp_path: Path) -> None:
    out = io.BytesIO()
    picture = Image.new("RGB", (4, 4))
    picture.putpixel((1, 1), (255, 128, 0))
    picture.save(out, format="TIFF")
    _package(tmp_path, {FLAME: out.getvalue()})
    specs = {"flame": ParticleSpec(FLAME, (2, 2), 64, "luminance", "MediumFlames", "Flames.")}
    rows = particle_rows(find_packages(tmp_path / "cache")[0], tmp_path / "assets", specs)
    assert rows[0]["alpha"] == "luminance"
    with Image.open(tmp_path / rows[0]["path"]) as image:
        assert image.getpixel((0, 0)) == (0, 0, 0, 0) and image.getpixel((1, 1)) == (255, 128, 0, 255)
