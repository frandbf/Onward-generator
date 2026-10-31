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


def test_the_checklist_asks_the_headset_about_shop_glass_and_keeps_the_walk_through_risk_open() -> None:
    start = QUEST_CHECKLIST.index("La iluminación y los materiales son correctos en el dispositivo.") + 1
    assert QUEST_CHECKLIST[start:start + 5] == (
        "Escaparates de tienda: vidrio sin marco, visible desde la calle y desde dentro (anotar qué material de vidrio registró el adaptador).",
        "Un disparo atraviesa el escaparate de una tienda sin impacto en el vidrio.",
        "Riesgo abierto: el escaparate no tiene colisión sobre el zócalo de 0,25 m. Anotar si el jugador entra a la tienda por él.",
        "El nombre de la tienda en el escaparate se lee en letras negras desde la calle y no tapa el interior.",
        "Los interiores de tienda muestran suelo y paredes con textura.",
    )
    assert QUEST_CHECKLIST[start + 5].startswith("La tasa de fotogramas") and len(QUEST_CHECKLIST) == 21
