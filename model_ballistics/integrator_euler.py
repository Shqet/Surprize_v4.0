# integrator_euler.py
"""
Step 8: Assemble full RHS for 18-state system + Euler integrator + dt convergence check

Assumptions (variant B contract):
- v is inertial
- omega is body
- alpha is body->inertial (stored row-major in state)
- after each Euler step, alpha is orthonormalized (Gram–Schmidt + det fix)
- n_hat degeneracy handled with prev_n_hat memory inside integrator

Files expected in project root (from previous steps):
  - atmosphere_isa.py   (ISA)
  - aerodynamics.py   (aero helpers)
  - lift_geometry.py   (n_hat)
  - translational_dynamics.py   (translational accel)
  - rotational_dynamics.py   (stabilizing moment + Euler rotation)
  - orientation_kinematics.py   (Poisson + orthonorm)

This file provides:
- SimParams: dt, t_max, max_steps
- FullParams: phys + rot
- make_initial_state(...)
- simulate_euler_full(...)
- quick convergence helper: convergence_sanity(...)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any
import math
import numpy as np

from translational_dynamics import PhysParams, accel_inertial_from_state
from rotational_dynamics import RotParams, rotational_rhs_from_state
from orientation_kinematics import alpha_dot_poisson, orthonormalize_alpha_columns, ortho_error_norm


# ----------------- state layout (must match previous steps) -----------------
IDX_X, IDX_Y, IDX_Z = 0, 1, 2
IDX_VX, IDX_VY, IDX_VZ = 3, 4, 5
IDX_WX, IDX_WY, IDX_WZ = 6, 7, 8
IDX_A0 = 9  # alpha starts here (9 elements), row-major


def alpha_from_state(y: np.ndarray) -> np.ndarray:
    return y[IDX_A0:IDX_A0 + 9].reshape(3, 3)


def set_alpha_in_state(y: np.ndarray, A: np.ndarray) -> None:
    y[IDX_A0:IDX_A0 + 9] = np.asarray(A, dtype=float).reshape(9)


def make_initial_state(
    X: float, Y: float, Z: float,
    Vx: float, Vy: float, Vz: float,
    wx: float = 0.0, wy: float = 0.0, wz: float = 0.0,
    alpha0: Optional[np.ndarray] = None,
) -> np.ndarray:
    y0 = np.zeros(18, dtype=float)
    y0[IDX_X:IDX_Z + 1] = [X, Y, Z]
    y0[IDX_VX:IDX_VZ + 1] = [Vx, Vy, Vz]
    y0[IDX_WX:IDX_WZ + 1] = [wx, wy, wz]
    if alpha0 is None:
        alpha0 = np.eye(3, dtype=float)
    set_alpha_in_state(y0, alpha0)
    return y0


# ----------------- simulation parameters -----------------
@dataclass(frozen=True)
class SimParams:
    dt: float
    t_max: float
    max_steps: int = 2_000_000  # hard safety


@dataclass(frozen=True)
class FullParams:
    phys: PhysParams
    rot: RotParams


# ----------------- full RHS (dy/dt) -----------------
def rhs_full(
    y: np.ndarray,
    t: float,
    P: FullParams,
    prev_n_hat: Optional[np.ndarray],
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Returns:
      dy      : (18,) time derivative
      n_hat   : (3,) lift normal used (for reuse on degeneracy)
      diag    : dict of diagnostics (optional, useful for debugging)
    """
    dy = np.zeros(18, dtype=float)

    # 1) kinematics
    dy[IDX_X] = y[IDX_VX]
    dy[IDX_Y] = y[IDX_VY]
    dy[IDX_Z] = y[IDX_VZ]

    # 2) translational acceleration (in inertial)
    a_I, n_hat = accel_inertial_from_state(y, P.phys, prev_n_hat=prev_n_hat)
    dy[IDX_VX:IDX_VZ + 1] = a_I

    # 3) rotational dynamics (in body): omega_dot_B
    v_hat_I, v_hat_B, M_B, wdot_B = rotational_rhs_from_state(y, P.rot)
    dy[IDX_WX:IDX_WZ + 1] = wdot_B

    # 4) Poisson kinematics for alpha: alpha_dot = alpha * [omega]_x
    A = alpha_from_state(y)
    dA = alpha_dot_poisson(A, np.array([y[IDX_WX], y[IDX_WY], y[IDX_WZ]], dtype=float))
    dy[IDX_A0:IDX_A0 + 9] = dA.reshape(9)

    diag = {
        "M_B": M_B,
        "v_hat_B": v_hat_B,
        "ortho_err": ortho_error_norm(A),
    }
    return dy, n_hat, diag


# ----------------- Euler integrator (full) -----------------
def simulate_euler_full(
    y0: np.ndarray,
    sim: SimParams,
    P: FullParams,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Fixed-step Euler integration with:
      - alpha orthonormalization after each step
      - stop on ground (Z <= 0) or t_max or max_steps
      - NaN/Inf guards

    Returns:
      t_hist: (N,)
      Y_hist: (N,18)
      info  : dict (reason, ortho_err_max, steps)
    """
    dt = float(sim.dt)
    if dt <= 0:
        raise ValueError("dt must be > 0")
    if sim.t_max <= 0:
        raise ValueError("t_max must be > 0")
    if sim.max_steps <= 0:
        raise ValueError("max_steps must be > 0")
    if y0.shape != (18,):
        raise ValueError("y0 must be shape (18,)")

    n_steps_time = int(math.floor(sim.t_max / dt))
    n_steps = min(n_steps_time, sim.max_steps)

    # allocate worst-case, then trim
    t_hist = np.empty(n_steps + 1, dtype=float)
    Y_hist = np.empty((n_steps + 1, 18), dtype=float)

    t = 0.0
    y = y0.astype(float, copy=True)

    # store initial
    t_hist[0] = t
    Y_hist[0] = y

    prev_n_hat: Optional[np.ndarray] = None
    ortho_err_max = ortho_error_norm(alpha_from_state(y))
    reason = "t_max"
    k = 0

    for i in range(1, n_steps + 1):
        dy, n_hat, diag = rhs_full(y, t, P, prev_n_hat=prev_n_hat)

        # NaN/Inf guard on derivative
        if not np.all(np.isfinite(dy)):
            reason = "nonfinite_dy"
            break

        y = y + dt * dy
        t = t + dt

        # Re-orthonormalize alpha (numerical stabilization allowed)
        A = alpha_from_state(y)
        A = orthonormalize_alpha_columns(A)
        set_alpha_in_state(y, A)

        # Store
        t_hist[i] = t
        Y_hist[i] = y
        k = i

        # Update prev_n_hat for next step
        prev_n_hat = n_hat

        # Diagnostics
        ortho_err = ortho_error_norm(A)
        ortho_err_max = max(ortho_err_max, ortho_err)

        # Stop on ground
        if y[IDX_Z] <= 0.0:
            reason = "ground"
            break

        # NaN/Inf guard on state
        if not np.all(np.isfinite(y)):
            reason = "nonfinite_state"
            break

    info = {
        "reason": reason,
        "steps": k,
        "t_end": float(t_hist[k]),
        "ortho_err_max": float(ortho_err_max),
        "dt": dt,
        "t_max": sim.t_max,
    }
    return t_hist[:k + 1], Y_hist[:k + 1], info


# ----------------- Convergence sanity helper (Step 8 check) -----------------
def impact_metrics(t: np.ndarray, Y: np.ndarray) -> Dict[str, float]:
    """
    Simple metrics at final sample (Step 9 will add interpolation).
    """
    Xf, Yf, Zf = float(Y[-1, IDX_X]), float(Y[-1, IDX_Y]), float(Y[-1, IDX_Z])
    tf = float(t[-1])
    rng = math.sqrt(Xf * Xf + Yf * Yf)
    return {"t_end": tf, "X_end": Xf, "Y_end": Yf, "Z_end": Zf, "range_xy": rng}


def convergence_sanity(
    y0: np.ndarray,
    P: FullParams,
    dt0: float,
    t_max: float,
) -> Dict[str, Dict[str, float]]:
    """
    Run 3 sims with dt0, dt0/2, dt0/4 and return impact metrics.
    Expect (for a stable dt0) that metrics move less and less as dt shrinks.
    """
    out: Dict[str, Dict[str, float]] = {}
    for k, dt in enumerate([dt0, dt0 / 2.0, dt0 / 4.0], start=0):
        sim = SimParams(dt=dt, t_max=t_max)
        t, Y, info = simulate_euler_full(y0, sim, P)
        m = impact_metrics(t, Y)
        # include a couple of diagnostics
        m["ortho_err_max"] = float(info["ortho_err_max"])
        m["reason"] = 0.0 if info["reason"] == "ground" else 1.0  # numeric marker
        out[f"dt={dt:g}"] = m
    return out


# ----------------- Step 8 control checks (runnable) -----------------
def _step8_checks():
    # Choose a "reasonable" toy set of parameters (not claiming real projectile values).
    # These are just to validate that the integrator runs and doesn't blow up.
    phys = PhysParams(
        m=10.0,         # kg
        S=0.01,         # m^2
        C_L=0.0,        # set 0 initially to satisfy check 1 (reasonable fall)
        C_mp=0.0,       # set 0 initially to satisfy check 1
        g=9.81
    )
    rot = RotParams(
        Ix=0.02, Iy=0.10, Iz=0.10,
        k_stab=1.0
    )
    P = FullParams(phys=phys, rot=rot)

    # Initial state: fired from height 1 m, modest elevation
    y0 = make_initial_state(
        X=0.0, Y=0.0, Z=1.0,
        Vx=300.0, Vy=0.0, Vz=80.0,
        wx=0.0, wy=0.0, wz=100.0,  # arbitrary spin
        alpha0=np.eye(3)
    )

    # 1) Run with small dt and ensure no NaN and alpha doesn't drift
    sim = SimParams(dt=1e-3, t_max=60.0)
    t, Y, info = simulate_euler_full(y0, sim, P)
    assert info["reason"] in ("ground", "t_max"), f"Unexpected stop reason: {info}"
    assert np.all(np.isfinite(Y)), "State contains NaN/Inf"
    assert info["ortho_err_max"] < 1e-6, f"Alpha drift too large: {info['ortho_err_max']}"

    # 2) Convergence sanity: dt shrink should not explode
    conv = convergence_sanity(y0, P, dt0=2e-3, t_max=60.0)
    # Just ensure metrics are finite (the formal convergence assessment is visual/quantitative later)
    for k, m in conv.items():
        for kk, vv in m.items():
            assert math.isfinite(float(vv)), f"Non-finite metric {kk}={vv} for {k}"

    print("Step 8 checks: OK")
    print("Example convergence metrics:")
    for k, m in conv.items():
        print(k, m)


if __name__ == "__main__":
    _step8_checks()
