# impact_event.py
"""
Step 9: Ground impact detection with linear interpolation to Z=0

We extend Step 8 integrator:
- When Z crosses from >0 to <=0 between steps k-1 and k:
    interpolate lambda in [0,1]:
      Z_prev + lambda*(Z_curr - Z_prev) = 0
      lambda = -Z_prev / (Z_curr - Z_prev)
    then:
      t_imp = t_prev + lambda*dt
      y_imp = y_prev + lambda*(y_curr - y_prev)
    set y_imp[Z]=0 exactly
    re-orthonormalize alpha at impact (numerical stabilization)

We also provide:
- impact_metrics_interpolated(...) for stable range/time metrics
- convergence_sanity_impact(...) to check dt sensitivity with impact interpolation
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any
import math
import numpy as np

from integrator_euler import (
    IDX_X, IDX_Y, IDX_Z, IDX_A0,
    alpha_from_state, set_alpha_in_state,
    make_initial_state, rhs_full,
    SimParams, FullParams,
)
from orientation_kinematics import orthonormalize_alpha_columns, ortho_error_norm


def _interp_to_ground(
    t_prev: float, y_prev: np.ndarray,
    t_curr: float, y_curr: np.ndarray,
) -> Tuple[float, np.ndarray]:
    """
    Linear interpolation between prev and curr to find Z=0 impact.
    Assumes y_prev[Z] > 0 and y_curr[Z] <= 0 (a crossing).
    """
    Zp = float(y_prev[IDX_Z])
    Zc = float(y_curr[IDX_Z])
    dZ = Zc - Zp

    # If dZ is 0 (extremely unlikely for a proper crossing), fallback to current
    if abs(dZ) < 1e-15:
        t_imp = t_curr
        y_imp = y_curr.copy()
        y_imp[IDX_Z] = 0.0
        return t_imp, y_imp

    lam = -Zp / dZ  # should be in [0,1]
    # numerical clamp (just in case of tiny overshoot)
    lam = max(0.0, min(1.0, float(lam)))

    t_imp = float(t_prev + lam * (t_curr - t_prev))
    y_imp = y_prev + lam * (y_curr - y_prev)
    y_imp = y_imp.astype(float, copy=False)
    y_imp[IDX_Z] = 0.0
    return t_imp, y_imp


def simulate_euler_full_with_impact(
    y0: np.ndarray,
    sim: SimParams,
    P: FullParams,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """
    Like Step 8 simulate_euler_full, but if ground is crossed, we replace the last sample
    by interpolated impact sample where Z==0 exactly, and stop.

    Returns:
      t_hist: (N,)
      Y_hist: (N,18)
      info  : dict(reason, steps, t_end, ortho_err_max, impact_interpolated)
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

    t_hist = np.empty(n_steps + 1, dtype=float)
    Y_hist = np.empty((n_steps + 1, 18), dtype=float)

    t = 0.0
    y = y0.astype(float, copy=True)

    t_hist[0] = t
    Y_hist[0] = y

    prev_n_hat: Optional[np.ndarray] = None
    ortho_err_max = ortho_error_norm(alpha_from_state(y))
    reason = "t_max"
    impact_interpolated = False

    k = 0
    for i in range(1, n_steps + 1):
        y_prev = y.copy()
        t_prev = t

        dy, n_hat, diag = rhs_full(y, t, P, prev_n_hat=prev_n_hat)

        if not np.all(np.isfinite(dy)):
            reason = "nonfinite_dy"
            break

        y = y + dt * dy
        t = t + dt

        # Orthonormalize alpha after step
        A = alpha_from_state(y)
        A = orthonormalize_alpha_columns(A)
        set_alpha_in_state(y, A)

        # store provisional
        t_hist[i] = t
        Y_hist[i] = y
        k = i

        prev_n_hat = n_hat

        ortho_err = ortho_error_norm(A)
        ortho_err_max = max(ortho_err_max, ortho_err)

        if not np.all(np.isfinite(y)):
            reason = "nonfinite_state"
            break

        # Ground crossing check:
        if float(y_prev[IDX_Z]) > 0.0 and float(y[IDX_Z]) <= 0.0:
            # interpolate impact point
            t_imp, y_imp = _interp_to_ground(t_prev, y_prev, t, y)

            # orthonormalize alpha at impact (because interpolation may slightly break it)
            A_imp = alpha_from_state(y_imp)
            A_imp = orthonormalize_alpha_columns(A_imp)
            set_alpha_in_state(y_imp, A_imp)

            # replace the last stored sample with impact
            t_hist[i] = t_imp
            Y_hist[i] = y_imp
            y = y_imp
            t = t_imp

            reason = "ground"
            impact_interpolated = True
            break

        # Also stop if we somehow land exactly without crossing logic
        if float(y[IDX_Z]) <= 0.0:
            y[IDX_Z] = 0.0
            reason = "ground"
            break

    info = {
        "reason": reason,
        "steps": k,
        "t_end": float(t_hist[k]),
        "ortho_err_max": float(ortho_err_max),
        "dt": dt,
        "t_max": sim.t_max,
        "impact_interpolated": impact_interpolated,
    }
    return t_hist[:k + 1], Y_hist[:k + 1], info


# ----------------- Metrics + dt-sensitivity helper (with impact) -----------------
def impact_metrics_interpolated(t: np.ndarray, Y: np.ndarray) -> Dict[str, float]:
    Xf, Yf, Zf = float(Y[-1, IDX_X]), float(Y[-1, IDX_Y]), float(Y[-1, IDX_Z])
    tf = float(t[-1])
    rng = math.sqrt(Xf * Xf + Yf * Yf)
    return {"t_impact": tf, "X_impact": Xf, "Y_impact": Yf, "Z_impact": Zf, "range_xy": rng}


def convergence_sanity_impact(
    y0: np.ndarray,
    P: FullParams,
    dt0: float,
    t_max: float,
) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for dt in [dt0, dt0 / 2.0, dt0 / 4.0]:
        sim = SimParams(dt=dt, t_max=t_max)
        t, Y, info = simulate_euler_full_with_impact(y0, sim, P)
        m = impact_metrics_interpolated(t, Y)
        m["ortho_err_max"] = float(info["ortho_err_max"])
        m["impact_interpolated"] = 1.0 if info["impact_interpolated"] else 0.0
        out[f"dt={dt:g}"] = m
    return out


# ----------------- Step 9 control checks (runnable) -----------------
def _step9_checks():
    # Use same toy parameters as Step 8 check
    from translational_dynamics import PhysParams
    from rotational_dynamics import RotParams

    phys = PhysParams(m=10.0, S=0.01, C_L=0.0, C_mp=0.0, g=9.81)
    rot = RotParams(Ix=0.02, Iy=0.10, Iz=0.10, k_stab=1.0)
    P = FullParams(phys=phys, rot=rot)

    y0 = make_initial_state(
        X=0.0, Y=0.0, Z=1.0,
        Vx=300.0, Vy=0.0, Vz=80.0,
        wx=0.0, wy=0.0, wz=100.0,
        alpha0=np.eye(3),
    )

    # run with modest dt and ensure last Z==0 exactly when reason ground
    sim = SimParams(dt=2e-3, t_max=60.0)
    t, Y, info = simulate_euler_full_with_impact(y0, sim, P)
    if info["reason"] == "ground":
        assert abs(float(Y[-1, IDX_Z]) - 0.0) < 1e-12, f"Last Z not 0: {Y[-1, IDX_Z]}"
    assert np.all(np.isfinite(Y)), "NaN/Inf in trajectory"

    # dt sensitivity helper (not asserting strict convergence, just sanity)
    conv = convergence_sanity_impact(y0, P, dt0=4e-3, t_max=60.0)
    for k, m in conv.items():
        assert abs(m["Z_impact"]) < 1e-10, f"Impact Z should be ~0 for {k}"
        for kk, vv in m.items():
            assert math.isfinite(float(vv)), f"Non-finite metric {kk} for {k}"

    print("Step 9 checks: OK")
    print("Impact convergence metrics:")
    for k, m in conv.items():
        print(k, m)


if __name__ == "__main__":
    _step9_checks()
