"""Smoke tests: the scaffold imports and its declared deps are present."""

import importlib


def test_runner_package_imports():
    mod = importlib.import_module("runner")
    assert hasattr(mod, "__version__")


def test_core_deps_importable():
    for name in ("requests", "yaml", "jsonschema", "rfc8785", "playwright"):
        importlib.import_module(name)


def test_dev_matrix_parses():
    from pathlib import Path

    import yaml

    cfg_path = Path(__file__).resolve().parents[1] / "configs" / "dev-matrix.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data["N"] == 2
    assert data["transport"]["base_url"] == "http://localhost:8080/v1"
    ids = {c["config_id"] for c in data["configs"]}
    assert "gpt-5.6-sol--api--raw--dev" in ids
    assert "grok-4.5--api--raw--dev" in ids
