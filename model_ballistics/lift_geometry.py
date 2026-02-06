# lift_geometry.py
"""
Step 4: Lift geometry - compute n_hat and handle degeneracy safely

We define:
  e_x_body (in inertial) = first column of alpha (body->inertial)
  u = v x (e_x_body x v)
  n_hat = u / |u|

Safe logic:
- if |u| < eps_u:
    * if prev_n_hat provided and valid -> reuse it (renormalized)
    * else pick a stable fallback vector orthogonal to v_hat

Checks:
- |n_hat| ~ 1
- n_hat · v_hat ~ 0
- alpha=I and v along +X doesn't crash (u=0 case)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple
import math
import numpy as np

from aerodynamics import speed_and_vhat, AeroEps, EPS as AERO_EPS


@dataclass(frozen=True)
class NHatEps:
    u_eps: float = 1e-9   # threshold for |u| degeneracy


N_EPS = NHatEps()


def _norm(v: np.ndarray) -> float:
    return float(np.linalg.norm(v))


def _safe_unit(v: np.ndarray, eps: float) -> Tuple[np.ndarray, bool]:
    n = _norm(v)
    if not math.isfinite(n) or n < eps:
        return np.zeros(3, dtype=float), False
    return v / n, True


def ex_body_from_alpha(alpha: np.ndarray) -> np.ndarray:
    """
    alpha is 3x3 body->inertial. e_x_body in inertial is first column.
    """
    A = np.asarray(alpha, dtype=float).reshape(3, 3)
    return A[:, 0].copy()


def _fallback_orthogonal_to(v_hat: np.ndarray) -> np.ndarray:
    """
    Return a stable unit vector orthogonal to v_hat.
    Strategy:
    - choose a reference axis not too parallel to v_hat
    - take cross(ref, v_hat) and normalize -> gives vector orthogonal to v_hat
    """
    # If v_hat is near zero, return a fixed axis
    if _norm(v_hat) < 1e-12:
        return np.array([0.0, 0.0, 1.0], dtype=float)

    # pick axis with smallest absolute dot (most orthogonal)
    axes = [
        np.array([1.0, 0.0, 0.0], dtype=float),
        np.array([0.0, 1.0, 0.0], dtype=float),
        np.array([0.0, 0.0, 1.0], dtype=float),
    ]
    dots = [abs(float(np.dot(a, v_hat))) for a in axes]
    ref = axes[int(np.argmin(dots))]

    w = np.cross(ref, v_hat)
    w_hat, ok = _safe_unit(w, eps=1e-12)
    if ok:
        return w_hat

    # Extremely rare: ref parallel to v_hat due to numerical issues -> try another
    for ref2 in axes:
        w2 = np.cross(ref2, v_hat)
        w2_hat, ok2 = _safe_unit(w2, eps=1e-12)
        if ok2:
            return w2_hat

    # ultimate fallback
    return np.array([0.0, 0.0, 1.0], dtype=float)


def compute_n_hat(
    v_inertial: np.ndarray,
    alpha: np.ndarray,
    prev_n_hat: Optional[np.ndarray] = None,
    eps_u: NHatEps = N_EPS,
    aero_eps: AeroEps = AERO_EPS,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute (n_hat, v_hat) in inertial frame.

    Args:
      v_inertial: (3,) velocity in inertial frame
      alpha: (3,3) body->inertial rotation matrix
      prev_n_hat: previous step n_hat (optional)

    Returns:
      n_hat: (3,) lift normal unit vector (inertial)
      v_hat: (3,) unit velocity direction (inertial, may be zero vector if V tiny)
    """
    v = np.asarray(v_inertial, dtype=float).reshape(3,)
    v_speed, v_hat = speed_and_vhat(v, eps=aero_eps)

    exb = ex_body_from_alpha(alpha)

    # u = v x (exb x v)
    u = np.cross(v, np.cross(exb, v))
    u_norm = _norm(u)

    if math.isfinite(u_norm) and u_norm >= eps_u.u_eps:
        n_hat = u / u_norm
        return n_hat, v_hat

    # Degenerate: reuse previous if possible
    if prev_n_hat is not None:
        prev = np.asarray(prev_n_hat, dtype=float).reshape(3,)
        prev_hat, ok = _safe_unit(prev, eps=1e-12)
        if ok:
            # ensure orthogonal to v_hat if v_hat is meaningful (small correction)
            if _norm(v_hat) > 1e-12:
                # project out component along v_hat
                prev_hat = prev_hat - float(np.dot(prev_hat, v_hat)) * v_hat
                prev_hat, ok2 = _safe_unit(prev_hat, eps=1e-12)
                if ok2:
                    return prev_hat, v_hat
            return prev_hat, v_hat

    # No usable previous -> construct fallback orthogonal to v_hat
    n_hat = _fallback_orthogonal_to(v_hat)
    return n_hat, v_hat


# ----------------- Step-4 sanity checks -----------------
def _check_unit_and_perp():
    # random velocity not parallel to ex_body, alpha=I
    alpha = np.eye(3)
    v = np.array([100.0, 30.0, 10.0])
    n_hat, v_hat = compute_n_hat(v, alpha)
    assert abs(_norm(n_hat) - 1.0) < 1e-9, f"|n_hat| not 1: {_norm(n_hat)}"
    assert abs(float(np.dot(n_hat, v_hat))) < 1e-9, f"n_hat not perp v_hat: dot={np.dot(n_hat, v_hat)}"


def _check_degenerate_start_no_crash():
    # alpha=I, v along x => ex_body parallel v => u=0 => must fallback safely
    alpha = np.eye(3)
    v = np.array([300.0, 0.0, 0.0])
    n_hat, v_hat = compute_n_hat(v, alpha, prev_n_hat=None)
    assert math.isfinite(_norm(n_hat)) and _norm(n_hat) > 0.0
    assert abs(_norm(n_hat) - 1.0) < 1e-9, "fallback n_hat must be unit"
    assert abs(float(np.dot(n_hat, v_hat))) < 1e-9, "fallback n_hat must be perp to v_hat"


def _check_prev_reuse():
    alpha = np.eye(3)
    v1 = np.array([300.0, 0.0, 0.0])  # degenerate
    prev = np.array([0.0, 0.0, 1.0])
    n_hat, v_hat = compute_n_hat(v1, alpha, prev_n_hat=prev)
    assert abs(_norm(n_hat) - 1.0) < 1e-12
    assert abs(float(np.dot(n_hat, v_hat))) < 1e-9


if __name__ == "__main__":
    _check_unit_and_perp()
    _check_degenerate_start_no_crash()
    _check_prev_reuse()
    print("Step 4 checks: OK")
