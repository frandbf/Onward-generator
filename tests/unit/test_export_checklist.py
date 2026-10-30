"""The headset checklist written into every export package."""

from __future__ import annotations

from onward_generator.export.package import QUEST_CHECKLIST, checklist


def test_checklist_text() -> None:
    manifest = {
        "map": {"title": "Murcia"},
        "package_id": "pkg-1",
        "adapter": {"name": "onward_unity", "version": "0.1.0"},
        "target": {"unity": "2021.3.23f1", "sdk": "1.15"},
    }
    text = checklist(manifest)
    rows = "\n".join(f"| {n} | {item} | | |" for n, item in enumerate(QUEST_CHECKLIST, start=1))
    assert text == (
        "# Prueba de aceptación en Quest — Murcia\n"
        "\n"
        "Paquete `pkg-1`, adaptador onward_unity 0.1.0, Unity 2021.3.23f1, SDK 1.15.\n"
        "Anota para cada punto: sí / no / no probado, y qué se observó. Ver docs/12-export.md (sección «Export package»).\n"
        "`device_verified` sigue en false hasta que este `quest_acceptance.md` esté completo en el visor.\n"
        "\n"
        "| # | Comprobación | Resultado | Observación |\n"
        "| --- | --- | --- | --- |\n"
        f"{rows}\n"
        "\n"
        "MD5 del `.content3` probado: (ver `build/content3.md5`)\n"
    )
    assert text.splitlines()[8] == f"| 1 | {QUEST_CHECKLIST[0]} | | |"
