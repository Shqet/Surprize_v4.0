from __future__ import annotations

from pathlib import Path

import pytest

from app.profiles import loader as loader_mod


def _write_profile(tmpdir: Path, name: str, yaml_text: str) -> None:
    (tmpdir / f"{name}.yaml").write_text(yaml_text, encoding="utf-8")


def _ballistics_block() -> str:
    # Minimal valid ballistics_model for loader v2
    return """
    ballistics_model:
      model_root: "model_ballistics"
      python_exe: "python"
      calc_entry: "run_vkr.py"
      plots_entry: "visualization.py"
      out_root: "outputs"
      timeout_sec: 1
      make_plots: false
      config_json: {}
    """.rstrip()


def test_profiles_loader_valid_v1_profile_has_stop_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Make loader read from tmp_path by spoofing its __file__
    monkeypatch.setattr(loader_mod, "__file__", str(tmp_path / "loader.py"))

    _write_profile(
        tmp_path,
        "p_v1",
        f"""
p_v1:
  orchestrator:
    stop_timeout_sec: 7
  services:
    exe_runner:
      path: "cmd"
      args: "/c echo hi"
      timeout_sec: 3
{_ballistics_block()}
""".lstrip(),
    )

    data = loader_mod.load_profile("p_v1")

    assert "p_v1" in data
    assert data["p_v1"]["orchestrator"]["stop_timeout_sec"] == 7
    assert data["p_v1"]["services"]["exe_runner"]["path"] == "cmd"


def test_profiles_loader_profile_without_stop_timeout_is_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # v1 rule: missing stop_timeout_sec must NOT break loader; default is applied by Orchestrator.
    monkeypatch.setattr(loader_mod, "__file__", str(tmp_path / "loader.py"))

    _write_profile(
        tmp_path,
        "p_no_timeout",
        f"""
p_no_timeout:
  services:
    exe_runner:
      path: "cmd"
      args: "/c echo hi"
      timeout_sec: 3
{_ballistics_block()}
""".lstrip(),
    )

    data = loader_mod.load_profile("p_no_timeout")

    assert "p_no_timeout" in data
    assert "orchestrator" not in data["p_no_timeout"]
