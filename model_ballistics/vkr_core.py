# vkr_core.py
# Общие функции для расчёта: конфиг -> параметры -> начальное состояние + IO helpers

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Dict, Any, Tuple

import numpy as np

from integrator_euler import SimParams, FullParams, make_initial_state, IDX_X, IDX_Y, IDX_Z
from translational_dynamics import PhysParams
from rotational_dynamics import RotParams


# ----------------- IO helpers -----------------
def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def save_csv(path: str, header: list[str], data_2d: np.ndarray) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    np.savetxt(path, data_2d, delimiter=",", header=",".join(header), comments="")


def save_json(path: str, obj: Dict[str, Any]) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------- config -> params/state -----------------
def build_params_and_state(cfg: Dict[str, Any]) -> Tuple[SimParams, FullParams, np.ndarray]:
    # Simulation params
    sim_cfg = cfg["simulation"]
    sim = SimParams(
        dt=float(sim_cfg["dt"]),
        t_max=float(sim_cfg["t_max"]),
        max_steps=int(sim_cfg.get("max_steps", 2_000_000)),
    )

    # Physical params
    phys_cfg = cfg["projectile"]
    phys = PhysParams(
        m=float(phys_cfg["m"]),
        S=float(phys_cfg["S"]),
        C_L=float(phys_cfg.get("C_L", 0.0)),
        C_mp=float(phys_cfg.get("C_mp", 0.0)),
        g=float(phys_cfg.get("g", 9.81)),
    )

    # Rotational params
    rot_cfg = cfg["rotation"]
    rot = RotParams(
        Ix=float(rot_cfg["Ix"]),
        Iy=float(rot_cfg["Iy"]),
        Iz=float(rot_cfg["Iz"]),
        k_stab=float(rot_cfg["k_stab"]),
    )

    P = FullParams(phys=phys, rot=rot)

    # Initial conditions
    ic = cfg["initial_conditions"]

    X0 = float(ic.get("X0", 0.0))
    Y0 = float(ic.get("Y0", 0.0))
    Z0 = float(ic["Z0"])

    # Support two formats:
    # 1) Old: Vx0, Vy0, Vz0 (+ wx0, wy0, wz0)
    # 2) New: V0, theta_deg, psi_deg (+ omega_body=[wx,wy,wz])
    if all(k in ic for k in ("Vx0", "Vy0", "Vz0")):
        Vx0 = float(ic["Vx0"])
        Vy0 = float(ic["Vy0"])
        Vz0 = float(ic["Vz0"])
    else:
        V0 = float(ic["V0"])
        theta = np.deg2rad(float(ic["theta_deg"]))
        psi = np.deg2rad(float(ic.get("psi_deg", 0.0)))

        Vx0 = V0 * np.cos(theta) * np.cos(psi)
        Vy0 = V0 * np.cos(theta) * np.sin(psi)
        Vz0 = V0 * np.sin(theta)

    if "omega_body" in ic:
        wx0, wy0, wz0 = map(float, ic["omega_body"])
    else:
        wx0 = float(ic.get("wx0", 0.0))
        wy0 = float(ic.get("wy0", 0.0))
        wz0 = float(ic.get("wz0", 0.0))

    y0 = make_initial_state(
        X=X0, Y=Y0, Z=Z0,
        Vx=Vx0, Vy=Vy0, Vz=Vz0,
        wx=wx0, wy=wy0, wz=wz0,
        alpha0=np.eye(3),
    )

    return sim, P, y0


def make_run_json(cfg: Dict[str, Any], sim: SimParams, P: FullParams,
                  info: Dict[str, Any], impact: Dict[str, Any],
                  diag: Dict[str, np.ndarray], Y: np.ndarray) -> Dict[str, Any]:
    # небольшой helper чтобы run_vkr.py был коротким
    return {
        "config": cfg,
        "sim_params": {"dt": sim.dt, "t_max": sim.t_max, "max_steps": sim.max_steps},
        "phys_params": asdict(P.phys),
        "rot_params": asdict(P.rot),
        "run_info": info,
        "impact": impact,
        "final_state": {
            "X": float(Y[-1, IDX_X]),
            "Y": float(Y[-1, IDX_Y]),
            "Z": float(Y[-1, IDX_Z]),
        },
        "sanity": {
            "Cd_min": float(np.min(diag["Cd"])),
            "Cd_max": float(np.max(diag["Cd"])),
            "detA_min": float(np.min(diag["detA"])),
            "detA_max": float(np.max(diag["detA"])),
            "ortho_err_max": float(np.max(diag["ortho_err"])),
            "n_norm_max_abs_err": float(np.max(np.abs(diag["n_norm"] - 1.0))),
            "n_dot_vhat_max_abs": float(np.max(np.abs(diag["n_dot_vhat"]))),
        }
    }
