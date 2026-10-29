"""Configuration diagnostics: field pointer, severity and message."""

from __future__ import annotations

from onward_generator.config.diagnostics import Diagnostic, has_errors


def test_diagnostic_serialises_every_field() -> None:
    d = Diagnostic("warning", "area_large", "/area/margin_m", "margin is large")
    assert d.to_dict() == {"severity": "warning", "code": "area_large", "path": "/area/margin_m", "message": "margin is large"}


def test_diagnostic_text_names_the_field_and_the_code() -> None:
    assert str(Diagnostic("error", "schema", "/area/margin_m", "must be >= 0")) == "ERROR   /area/margin_m: must be >= 0 [schema]"
    assert str(Diagnostic("info", "note", "", "whole document")) == "INFO    /: whole document [note]"


def test_has_errors_only_for_error_severity() -> None:
    warning = Diagnostic("warning", "w", "/a", "w")
    info = Diagnostic("info", "i", "", "i")
    error = Diagnostic("error", "e", "/b", "e")
    assert not has_errors([])
    assert not has_errors([warning, info])
    assert has_errors([warning, error])
    assert has_errors(iter([error]))
