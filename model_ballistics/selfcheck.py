"""
Step 12 — Minimal readiness self-check for VKR

This script verifies that the ballistic model:
- always terminates (ground or t_max / max_steps)
- produces no NaN / Inf
- keeps alpha as a valid rotation matrix (orthonormal + det≈1)
- successfully generates all required plots (PNG files)

Usage:
  python selfcheck.py

Exit codes:
  0 — PASS
  1 — FAIL
"""

import os
import sys
import json
import math
import numpy as np

from impact_event import simulate_euler_full_with_impact
from diagnostics import compute_diagnostics
from integrator_euler import (
    SimParams, FullParams, make_initial_state,
    IDX_X, IDX_Y, IDX_Z
)
from translational_dynamics import PhysParams
from rotational_dynamics import RotParams
from visualization import save_plots  # reuse plotting logic


# ----------------- thresholds (engineering tolerances) -----------------
TOL_ORTHO = 1e-6          # ||A^T A - I||_F
TOL_DET_MIN = 0.995
TOL_DET_MAX = 1.005
TOL_N_NORM = 1e-6
TOL_N_DOT_V = 1e-6


def run_case(name: str, phys: PhysParams, rot: RotParams, dt: float):
    sim = SimParams(dt=dt, t_max=120.0)
    P = FullParams(phys=phys, rot=rot)

    y0 = make_initial_state(
        X=0.0, Y=0.0, Z=1.0,
        Vx=300.0, Vy=0.0, Vz=80.0,
        wx=0.0, wy=0.0, wz=100.0,
    )

    t, Y, info = simulate_euler_full_with_impact(y0, sim, P)
    diag = compute_diagnostics(t, Y, P)

    return {
        "name": name,
        "t": t,
        "Y": Y,
        "info": info,
        "diag": diag,
    }


def check_case(result: dict):
    diag = result["diag"]
    info = result["info"]

    checks = {}

    # 1. Integration termination
    checks["terminated"] = info["reason"] in ("ground", "t_max")

    # 2. No NaN / Inf
    checks["finite_state"] = np.all(np.isfinite(result["Y"]))
    for k, v in diag.items():
        checks[f"finite_diag_{k}"] = np.all(np.isfinite(v))

    # 3. Alpha properties
    detA = diag["detA"]
    ortho = diag["ortho_err"]

    checks["det_alpha_ok"] = (
        np.min(detA) >= TOL_DET_MIN and np.max(detA) <= TOL_DET_MAX
    )
    checks["ortho_ok"] = np.max(ortho) <= TOL_ORTHO

    # 4. Lift geometry consistency
    checks["n_norm_ok"] = np.max(np.abs(diag["n_norm"] - 1.0)) <= TOL_N_NORM
    checks["n_dot_v_ok"] = np.max(np.abs(diag["n_dot_vhat"])) <= TOL_N_DOT_V

    return checks


def main():
    report = {
        "overall": "PASS",
        "cases": {}
    }

    # ----------------- test cases -----------------
    cases = [
        # Pure gravity (no aerodynamics)
        ("gravity_only",
         PhysParams(m=10.0, S=0.0, C_L=0.0, C_mp=0.0),
         RotParams(Ix=0.02, Iy=0.10, Iz=0.10, k_stab=0.0),
         0.005),

        # Drag + gravity
        ("drag_only",
         PhysParams(m=10.0, S=0.01, C_L=0.0, C_mp=0.0),
         RotParams(Ix=0.02, Iy=0.10, Iz=0.10, k_stab=0.0),
         0.002),

        # Full model (stabilization enabled)
        ("full_model",
         PhysParams(m=10.0, S=0.01, C_L=0.0, C_mp=0.0),
         RotParams(Ix=0.02, Iy=0.10, Iz=0.10, k_stab=1.0),
         0.002),
    ]

    # ----------------- run checks -----------------
    for name, phys, rot, dt in cases:
        result = run_case(name, phys, rot, dt)
        checks = check_case(result)

        report["cases"][name] = {
            "dt": dt,
            "termination_reason": result["info"]["reason"],
            "checks": checks
        }

        if not all(checks.values()):
            report["overall"] = "FAIL"

        # Save plots for visual confirmation
        plot_dir = os.path.join("selfcheck_plots", name)
        save_plots(plot_dir, result["t"], result["Y"], result["diag"])

    # ----------------- save report -----------------
    os.makedirs("selfcheck", exist_ok=True)
    report_path = os.path.join("selfcheck", "selfcheck_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # ----------------- console output -----------------
    print("\n=== SELF-CHECK REPORT ===")
    print(json.dumps(report, indent=2))
    print(f"\nReport saved to: {report_path}")

    if report["overall"] != "PASS":
        print("\nSELF-CHECK FAILED ❌")
        sys.exit(1)

    print("\nSELF-CHECK PASSED ✅")
    sys.exit(0)


if __name__ == "__main__":
    main()
