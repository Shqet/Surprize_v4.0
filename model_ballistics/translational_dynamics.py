# translational_dynamics.py
"""
Step 5: Translational equations - assemble acceleration in inertial frame

Forces in inertial frame:
  Drag   : F_D = - q * S * C_D(M) * v_hat
  Lift   : F_L = + q * S * C_L    * n_hat
  Magnus : F_M = + C_mp * rho * S * (omega_I x v_I)      where omega_I = alpha * omega_B
  Gravity: a_g = (0,0,-g) added as acceleration, not as force

Acceleration:
  a = (F_D + F_L + F_M)/m + (0,0,-g)

This module provides:
- accel_inertial_from_state(y, params, prev_n_hat) -> (a_I, n_hat)

And includes Step-5 control checks.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple
import math
import numpy as np

from aerodynamics import mach_q_from_state, C_D, speed_and_vhat, EPS as AERO_EPS
from lift_geometry import compute_n_hat, ex_body_from_alpha

# --- state layout (must match Step 1) ---
IDX_X, IDX_Y, IDX_Z = 0, 1, 2
IDX_VX, IDX_VY, IDX_VZ = 3, 4, 5
IDX_WX, IDX_WY, IDX_WZ = 6, 7, 8
IDX_A0 = 9  # alpha starts here (9 elements, row-major in our storage)


def alpha_from_state(y: np.ndarray) -> np.ndarray:
    return y[IDX_A0:IDX_A0 + 9].reshape(3, 3)


@dataclass(frozen=True)
class PhysParams:
    m: float            # kg
    S: float            # m^2
    C_L: float          # dimensionless
    C_mp: float         # meters (so that C_mp*rho*S*(omega x v) has units of N)
    g: float = 9.81     # m/s^2


def accel_inertial_from_state(
    y: np.ndarray,
    p: PhysParams,
    prev_n_hat: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute translational acceleration in inertial frame:
      dV/dt = a_I

    Returns:
      a_I   : (3,) acceleration [m/s^2]
      n_hat : (3,) lift normal unit vector used (inertial)
    """
    if p.m <= 0:
        raise ValueError("Mass m must be > 0")
    if p.S < 0:
        raise ValueError("Area S must be >= 0")

    # unpack state
    Z = float(y[IDX_Z])
    v_I = np.array([y[IDX_VX], y[IDX_VY], y[IDX_VZ]], dtype=float)
    w_B = np.array([y[IDX_WX], y[IDX_WY], y[IDX_WZ]], dtype=float)
    A = alpha_from_state(y)  # body -> inertial

    # speed + v_hat (safe)
    V, v_hat = speed_and_vhat(v_I, eps=AERO_EPS)

    # Atmosphere, Mach, q
    rho, a, M, q = mach_q_from_state(v_I, h_m=Z, eps=AERO_EPS)

    # Drag coefficient
    cd = C_D(M, eps=AERO_EPS)

    # Lift direction n_hat (safe, may fallback)
    n_hat, _ = compute_n_hat(v_I, A, prev_n_hat=prev_n_hat)

    # Forces
    F = np.zeros(3, dtype=float)

    # Drag: only if speed meaningful
    if V >= AERO_EPS.v_eps:
        F_drag = -(q * p.S * cd) * v_hat
    else:
        F_drag = np.zeros(3, dtype=float)

    # Lift
    F_lift = (q * p.S * p.C_L) * n_hat

    # Magnus-like term: omega_I = A * omega_B
    omega_I = A @ w_B
    F_mag = (p.C_mp * rho * p.S) * np.cross(omega_I, v_I)

    F = F_drag + F_lift + F_mag

    a_I = (F / p.m) + np.array([0.0, 0.0, -p.g], dtype=float)
    return a_I, n_hat


# ----------------- Step-5 control checks -----------------
def _check_drag_opposes_velocity():
    """
    Check: F_D · v <= 0 (for V>0)
    """
    p = PhysParams(m=10.0, S=0.01, C_L=0.0, C_mp=0.0, g=9.81)
    y = np.zeros(18, dtype=float)
    # Z sea level, v any direction
    y[IDX_Z] = 0.0
    y[IDX_VX:IDX_VZ + 1] = [200.0, -50.0, 20.0]
    # alpha = I
    y[IDX_A0:IDX_A0 + 9] = np.eye(3).reshape(9)

    # compute pieces by reusing functions inside
    Z = float(y[IDX_Z])
    v_I = np.array([y[IDX_VX], y[IDX_VY], y[IDX_VZ]], dtype=float)
    V, v_hat = speed_and_vhat(v_I, eps=AERO_EPS)
    rho, a, M, q = mach_q_from_state(v_I, h_m=Z, eps=AERO_EPS)
    cd = C_D(M, eps=AERO_EPS)
    F_drag = -(q * p.S * cd) * v_hat

    dot = float(np.dot(F_drag, v_I))
    assert dot <= 1e-9, f"Drag must oppose velocity: F_drag·v={dot}"


def _check_gravity_only_gives_minus_g():
    """
    If aeroforces are zero (S=0 or cd=cl=cmp=0), then dVz/dt = -g.
    We'll set S=0 to force all aerodynamic forces to zero.
    """
    p = PhysParams(m=10.0, S=0.0, C_L=0.0, C_mp=0.0, g=9.81)
    y = np.zeros(18, dtype=float)
    y[IDX_Z] = 100.0
    y[IDX_VX:IDX_VZ + 1] = [100.0, 0.0, 0.0]
    y[IDX_A0:IDX_A0 + 9] = np.eye(3).reshape(9)

    a_I, _ = accel_inertial_from_state(y, p, prev_n_hat=None)
    assert abs(float(a_I[2]) + p.g) < 1e-12, f"Expected a_z=-g, got {a_I[2]}"
    assert abs(float(a_I[0])) < 1e-12 and abs(float(a_I[1])) < 1e-12, "No horizontal accel expected"


def _check_reasonable_when_CL_Cmp_zero():
    """
    With CL=Cmp=0 and S>0, only drag + gravity.
    We can't assert full trajectory here, but we can assert:
      - vertical accel is <= -g? (drag may add upward if v_z<0; but drag opposes v)
      - speed decreases due to drag (for V>0, F_drag·v <= 0)
    """
    p = PhysParams(m=10.0, S=0.01, C_L=0.0, C_mp=0.0, g=9.81)
    y = np.zeros(18, dtype=float)
    y[IDX_Z] = 100.0
    y[IDX_VX:IDX_VZ + 1] = [300.0, 0.0, -10.0]  # descending slightly
    y[IDX_A0:IDX_A0 + 9] = np.eye(3).reshape(9)

    a_I, _ = accel_inertial_from_state(y, p, prev_n_hat=None)

    # Drag should reduce forward speed: a_x should be negative here
    assert float(a_I[0]) < 0.0, f"Expected drag to decelerate in X, got a_x={a_I[0]}"

    # If descending (v_z<0), drag adds upward component, so a_z should be > -g (less negative).
    assert float(a_I[2]) > -p.g - 1e-9, f"Expected a_z >= -g when v_z<0 due to drag, got a_z={a_I[2]}"


if __name__ == "__main__":
    _check_drag_opposes_velocity()
    _check_gravity_only_gives_minus_g()
    _check_reasonable_when_CL_Cmp_zero()
    print("Step 5 checks: OK")
