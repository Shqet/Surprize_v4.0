# run_vkr.py
# Чистый расчёт (без графиков): config -> simulate -> outputs/*.csv + run.json

from __future__ import annotations

import argparse
import os
import numpy as np

from impact_event import simulate_euler_full_with_impact, impact_metrics_interpolated
from diagnostics import compute_diagnostics, print_control_checks

from vkr_core import (
    load_config,
    build_params_and_state,
    ensure_dir,
    save_csv,
    save_json,
    make_run_json,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="vkr_config.json", help="JSON конфиг (по умолчанию vkr_config.json)")
    ap.add_argument("--out", default="outputs", help="Папка вывода (по умолчанию outputs)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    out_dir = args.out
    ensure_dir(out_dir)

    sim, P, y0 = build_params_and_state(cfg)

    t, Y, info = simulate_euler_full_with_impact(y0, sim, P)
    impact = impact_metrics_interpolated(t, Y)
    diag = compute_diagnostics(t, Y, P)

    # Быстрые проверки в консоль
    print("Simulation info:", info)
    print("Impact:", impact)
    print_control_checks(diag)

    # trajectory.csv: (t + 18 состояний)
    traj_mat = np.column_stack([t.reshape(-1, 1), Y])
    traj_header = [
        "t",
        "X", "Y", "Z",
        "Vx", "Vy", "Vz",
        "wx", "wy", "wz",
        "a11", "a12", "a13", "a21", "a22", "a23", "a31", "a32", "a33",
    ]
    save_csv(os.path.join(out_dir, "trajectory.csv"), traj_header, traj_mat)

    # diagnostics.csv
    diag_mat = np.column_stack([
        t,
        diag["V"], diag["M"], diag["Cd"], diag["q"], diag["rho"], diag["a"],
        diag["n_norm"], diag["n_dot_vhat"],
        diag["detA"], diag["ortho_err"],
        diag["vhatBy_abs"], diag["vhatBz_abs"],
    ])
    diag_header = [
        "t",
        "V", "M", "C_D", "q", "rho", "a",
        "n_norm", "n_dot_vhat",
        "detA", "ortho_err",
        "vhatBy_abs", "vhatBz_abs",
    ]
    save_csv(os.path.join(out_dir, "diagnostics.csv"), diag_header, diag_mat)

    # run.json
    run_json = make_run_json(cfg, sim, P, info, impact, diag, Y)
    save_json(os.path.join(out_dir, "run.json"), run_json)

    print(
        "Saved:\n"
        f"- {os.path.join(out_dir, 'run.json')}\n"
        f"- {os.path.join(out_dir, 'trajectory.csv')}\n"
        f"- {os.path.join(out_dir, 'diagnostics.csv')}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
