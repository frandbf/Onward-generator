"""The local .info3 listing and the opt-in headset slot candidates (ADR-007, amendment 1)."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from onward_generator.export.info3 import (
    CANDIDATE_LISTINGS,
    DEVICE_CONTENT_DIR,
    LOCAL_ID,
    Info3Error,
    apply_local_listing,
    candidate_listing,
    check_listing_thumbnail,
    device_thumbnail_url,
    listing_item_id,
    write_candidate_listing,
)

BUNDLE = b"bundle bytes"
BUNDLE_MD5 = hashlib.md5(BUNDLE).hexdigest()


def _local_doc(bundle: str = BUNDLE_MD5) -> dict[str, Any]:
    return {
        "WorkshopItem": {
            "ID": LOCAL_ID, "MapName": "Unity Export", "AvgRating": 0.0, "GameModes": [4, 10, 1],
            "ThumbnailUrl": "", "BundleHash": bundle, "Description": "Murcia: generated.",
        },
        "IsSanitized": False,
    }


def _write(path: Path, doc: object) -> Path:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "info3 not found"),
        ("{bad", "not strict JSON"),
        ('{"a": 1, "a": 2}', "not strict JSON"),
        ("[]", "root must be a JSON object"),
        ('{"WorkshopItem": []}', "missing WorkshopItem object"),
        ('{"IsSanitized": false}', "missing WorkshopItem object"),
        ('{"WorkshopItem": {"ID": "unity-export.local"}}', "BundleHash is missing"),
        ('{"WorkshopItem": {"ID": "unity-export.local", "BundleHash": ""}}', "BundleHash is missing"),
        ('{"WorkshopItem": {"ID": "unity-export.local", "BundleHash": 5}}', "BundleHash is missing"),
        ('{"WorkshopItem": {"ID": "123", "BundleHash": "A"}}', "ID is '123'; only the local test slot unity-export.local may be relabelled"),
    ],
)
def test_listing_refuses_a_bad_info3(tmp_path: Path, content: str | None, message: str) -> None:
    path = tmp_path / "x.info3"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(Info3Error, match=message):
        apply_local_listing(path, title="T", description="D")
    if content is not None:
        assert path.read_text(encoding="utf-8") == content


def test_local_listing_text_and_limits(tmp_path: Path) -> None:
    path = _write(tmp_path / "unity-export.local.info3", _local_doc())
    doc = apply_local_listing(path, title="M" * 80, description=" Ñ" + "d" * 597 + " ", environment="Snow")
    item = doc["WorkshopItem"]
    assert item["Description"] == "Ñ" + "d" * 597
    assert item["EnvironmentType"] == 3
    assert path.read_text(encoding="utf-8") == json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n"
    for title, description in (("", "D"), ("  ", "D"), ("M" * 81, "D")):
        with pytest.raises(Info3Error, match="^title must be 1–80 characters$"):
            apply_local_listing(path, title=title, description=description)
    for description in ("", " ", "d" * 601):
        with pytest.raises(Info3Error, match="^description must be 1–600 characters$"):
            apply_local_listing(path, title="T", description=description)
    with pytest.raises(Info3Error, match="'Mars' is not a LevelDescriptor.EMapEnvironment name"):
        apply_local_listing(path, title="T", description="D", environment="Mars")
    untouched = apply_local_listing(path, title="T", description="D")
    assert untouched["WorkshopItem"]["EnvironmentType"] == 3


def test_slot_ids_and_thumbnail_url() -> None:
    assert CANDIDATE_LISTINGS == ("local-thumb", "named", "named-thumb")
    assert listing_item_id("local-thumb", "not checked here") == LOCAL_ID
    assert listing_item_id("named", "Murcia_01") == "onwardgen.Murcia_01"
    assert listing_item_id("named-thumb", "m" * 40) == "onwardgen." + "m" * 40
    for map_id in ("", "m" * 41, "a-b", "a.b"):
        with pytest.raises(Info3Error, match=r"must match \[A-Za-z0-9_\]\{1,40\}"):
            listing_item_id("named", map_id)
    for slot in ("local", "LOCAL-THUMB", ""):
        with pytest.raises(Info3Error, match=f"listing mode {slot!r} is not one of local-thumb, named, named-thumb"):
            listing_item_id(slot, "m")
    assert DEVICE_CONTENT_DIR == "/sdcard/Android/data/com.downpourinteractive.onward/files/CustomContent"
    assert device_thumbnail_url("onwardgen.m") == f"file://{DEVICE_CONTENT_DIR}/onwardgen.m.png"


def test_slot_listing_fields_per_slot() -> None:
    source = _local_doc()
    before = json.dumps(source)
    thumb = candidate_listing(source, mode="local-thumb", title="Murcia", map_id="m")["WorkshopItem"]
    assert (thumb["ID"], thumb["MapName"]) == (LOCAL_ID, "Unity Export")
    assert thumb["ThumbnailUrl"] == f"file://{DEVICE_CONTENT_DIR}/unity-export.local.png"
    named = candidate_listing(source, mode="named", title=" Apocalipsis Murciana ", map_id="m")
    item = named["WorkshopItem"]
    assert (item["ID"], item["MapName"], item["ThumbnailUrl"]) == ("onwardgen.m", "Apocalipsis Murciana", "")
    assert (item["BundleHash"], item["GameModes"], named["IsSanitized"]) == (BUNDLE_MD5, [4, 10, 1], False)
    both = candidate_listing(source, mode="named-thumb", title="Murcia", map_id="m")["WorkshopItem"]
    assert (both["ID"], both["MapName"]) == ("onwardgen.m", "Murcia")
    assert both["ThumbnailUrl"] == f"file://{DEVICE_CONTENT_DIR}/onwardgen.m.png"
    assert json.dumps(source) == before
    with pytest.raises(Info3Error, match="title must be 1–80 characters"):
        candidate_listing(source, mode="named", title=" ", map_id="m")


def test_check_listing_thumbnail(tmp_path: Path) -> None:
    good = tmp_path / "good.png"
    good.write_bytes(_png(512, 256) + b"rest")
    assert check_listing_thumbnail(good) == (512, 256)
    exact = tmp_path / "exact.png"
    exact.write_bytes(_png(512, 256)[:24])
    assert check_listing_thumbnail(exact) == (512, 256)
    with pytest.raises(Info3Error, match="thumbnail not found"):
        check_listing_thumbnail(tmp_path / "missing.png")
    cases = {
        "jpeg.png": (b"\xff\xd8\xff\xe0" + _png(512, 256)[4:], "not a PNG"),
        "chunk.png": (_png(512, 256).replace(b"IHDR", b"IDAT"), "not a PNG"),
        "short.png": (b"\x89PNG\r\n\x1a\n", "not a PNG"),
        "cut.png": (_png(512, 256)[:23], "not a PNG"),
        "huge.png": (_png(0xFFFFFFFF, 256), "thumbnail is 4294967295×256"),
        "square.png": (_png(512, 512), "thumbnail is 512×512; the listing needs 512×256"),
        "small.png": (_png(256, 128), "thumbnail is 256×128"),
    }
    for name, (data, message) in cases.items():
        (tmp_path / name).write_bytes(data)
        with pytest.raises(Info3Error, match=message):
            check_listing_thumbnail(tmp_path / name)


def test_write_candidate_listing_never_overwrites(tmp_path: Path) -> None:
    content3 = tmp_path / "unity-export.local.content3"
    content3.write_bytes(BUNDLE)
    source = _write(tmp_path / "unity-export.local.info3", _local_doc(BUNDLE_MD5.upper()))
    source_text = source.read_text(encoding="utf-8")
    out = tmp_path / "build" / "slots" / "named"
    path = write_candidate_listing(source, out, mode="named", title="Murcia Ñ", map_id="m", content3=content3)
    assert path == out / "onwardgen.m.info3"
    expected = candidate_listing(_local_doc(BUNDLE_MD5.upper()), mode="named", title="Murcia Ñ", map_id="m")
    assert path.read_text(encoding="utf-8") == json.dumps(expected, ensure_ascii=False, separators=(",", ":")) + "\n"
    assert source.read_text(encoding="utf-8") == source_text
    # The same listing again is accepted; a different one is refused and the file kept.
    assert write_candidate_listing(source, out, mode="named", title="Murcia Ñ", map_id="m", content3=content3) == path
    kept = path.read_text(encoding="utf-8")
    with pytest.raises(Info3Error, match="exists with other content; candidate listings are never overwritten"):
        write_candidate_listing(source, out, mode="named", title="Other", map_id="m", content3=content3)
    assert path.read_text(encoding="utf-8") == kept
    # local-thumb never lands on the launcher's own listing.
    with pytest.raises(Info3Error, match="never overwritten"):
        write_candidate_listing(source, tmp_path, mode="local-thumb", title="Murcia", map_id="m", content3=content3)
    assert source.read_text(encoding="utf-8") == source_text
    # Another listing in a folder that already exists.
    beside = write_candidate_listing(source, out, mode="local-thumb", title="Murcia", map_id="m", content3=content3)
    assert beside == out / "unity-export.local.info3"
    assert path.read_text(encoding="utf-8") == kept


def test_write_candidate_listing_refuses_a_foreign_bundle(tmp_path: Path) -> None:
    content3 = tmp_path / "b.content3"
    content3.write_bytes(BUNDLE + b"x")
    source = _write(tmp_path / "unity-export.local.info3", _local_doc())
    out = tmp_path / "out"
    with pytest.raises(Info3Error, match=f"BundleHash {BUNDLE_MD5} is not the MD5 of"):
        write_candidate_listing(source, out, mode="named", title="M", map_id="m", content3=content3)
    with pytest.raises(Info3Error, match="content3 not found"):
        write_candidate_listing(source, out, mode="named", title="M", map_id="m", content3=tmp_path / "none.content3")
    other = _write(tmp_path / "w.info3", {"WorkshopItem": {"ID": "onwardgen.m", "BundleHash": BUNDLE_MD5}})
    content3.write_bytes(BUNDLE)
    with pytest.raises(Info3Error, match="only the local test slot"):
        write_candidate_listing(other, out, mode="named", title="M", map_id="m", content3=content3)
    assert not out.exists()
