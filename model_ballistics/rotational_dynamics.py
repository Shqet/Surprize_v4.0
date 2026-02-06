# rotational_dynamics.py
"""
Step 6: Stabilizing moment + Euler rotational equations in BODY frame

We assume (variant B contract):
- alpha is body->inertial
- omega^B is stored in state (wx, wy, wz)
- v^I is stored in state (Vx, Vy, Vz)
- v_hat^B = alpha^T * v_hat^I

Stabilizing moment (body):
  Mx = 0
  My = -k_stab * v_hat^B_z
  Mz = +k_stab * v_hat^B_y

Euler equations about principal axes (body):
  Ix * wdot_x - (Iy - Iz) * wy * wz = Mx
  Iy * wdot_y - (Iz - Ix) * wz * wx = My
  Iz * wdot_z - (Ix - Iy) * wx * wy = Mz

=> solve for wdot:
  wdot_x = (Mx + (Iy - Iz) * wy * wz) / Ix
  wdot_y = (My + (Iz - Ix) * wz * wx) / Iy
  wdot_z = (Mz + (Ix - Iy) * wx * wy) / Iz
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import math
import numpy as np

from aerodynamics import speed_and_vhat, EPS as AERO_EPS

# --- state layout (must match Step 1) ---
IDX_X, IDX_Y, IDX_Z = 0, 1, 2
IDX_VX, IDX_VY, IDX_VZ = 3, 4, 5
IDX_WX, IDX_WY, IDX_WZ = 6, 7, 8
IDX_A0 = 9  # alpha starts here (9 elements)


def alpha_from_state(y: np.ndarray) -> np.ndarray:
    return y[IDX_A0:IDX_A0 + 9].reshape(3, 3)


@dataclass(frozen=True)
class RotParams:
    Ix: float          # kg*m^2
    Iy: float          # kg*m^2
    Iz: float          # kg*m^2
    k_stab: float      # N*m (torque magnitude scale)


def vhat_inertial_and_body(v_I: np.ndarray, alpha: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Returns:
      v_hat_I: (3,) inertial
      v_hat_B: (3,) body
      V      : speed
    """
    V, v_hat_I = speed_and_vhat(v_I, eps=AERO_EPS)
    A = np.asarray(alpha, dtype=float).reshape(3, 3)
    v_hat_B = A.T @ v_hat_I
    return v_hat_I, v_hat_B, V


def stabilizing_moment_body(v_hat_B: np.ndarray, k_stab: float) -> np.ndarray:
    """
    Mx=0, My=-k*vhat_Bz, Mz=+k*vhat_By
    """
    vh = np.asarray(v_hat_B, dtype=float).reshape(3,)
    return np.array([0.0, -k_stab * float(vh[2]), +k_stab * float(vh[1])], dtype=float)


def omega_dot_body(omega_B: np.ndarray, M_B: np.ndarray, rp: RotParams) -> np.ndarray:
    """
    Euler equations for principal axes (body frame).
    """
    wx, wy, wz = float(omega_B[0]), float(omega_B[1]), float(omega_B[2])
    Mx, My, Mz = float(M_B[0]), float(M_B[1]), float(M_B[2])

    if rp.Ix <= 0 or rp.Iy <= 0 or rp.Iz <= 0:
        raise ValueError("Moments of inertia must be > 0")

    wdot_x = (Mx + (rp.Iy - rp.Iz) * wy * wz) / rp.Ix
    wdot_y = (My + (rp.Iz - rp.Ix) * wz * wx) / rp.Iy
    wdot_z = (Mz + (rp.Ix - rp.Iy) * wx * wy) / rp.Iz

    wdot = np.array([wdot_x, wdot_y, wdot_z], dtype=float)

    if not np.all(np.isfinite(wdot)):
        raise FloatingPointError(f"omega_dot contains non-finite values: {wdot}")

    return wdot


def rotational_rhs_from_state(y: np.ndarray, rp: RotParams) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Given full state y, compute:
      v_hat_I, v_hat_B, M_B, omega_dot_B
    """
    v_I = np.array([y[IDX_VX], y[IDX_VY], y[IDX_VZ]], dtype=float)
    omega_B = np.array([y[IDX_WX], y[IDX_WY], y[IDX_WZ]], dtype=float)
    A = alpha_from_state(y)

    v_hat_I, v_hat_B, V = vhat_inertial_and_body(v_I, A)
    M_B = stabilizing_moment_body(v_hat_B, rp.k_stab)
    wdot_B = omega_dot_body(omega_B, M_B, rp)
    return v_hat_I, v_hat_B, M_B, wdot_B


# ----------------- Step-6 control checks -----------------
def _check_aligned_gives_zero_moment():
    rp = RotParams(Ix=0.02, Iy=0.10, Iz=0.10, k_stab=1.0)
    y = np.zeros(18, dtype=float)
    # alpha=I -> body axes aligned with inertial
    y[IDX_A0:IDX_A0 + 9] = np.eye(3).reshape(9)
    # v along +X inertial -> v_hat_B = (1,0,0) -> moment ~ 0
    y[IDX_VX:IDX_VZ + 1] = [300.0, 0.0, 0.0]
    v_hat_I, v_hat_B, M_B, wdot_B = rotational_rhs_from_state(y, rp)
    assert np.linalg.norm(M_B) < 1e-12, f"Expected ~0 moment, got {M_B}"


def _check_crossflow_generates_moment():
    rp = RotParams(Ix=0.02, Iy=0.10, Iz=0.10, k_stab=2.0)
    y = np.zeros(18, dtype=float)
    y[IDX_A0:IDX_A0 + 9] = np.eye(3).reshape(9)

    # v has +Y component => v_hat_B_y > 0 => Mz = +k*v_hat_By > 0
    y[IDX_VX:IDX_VZ + 1] = [300.0, 30.0, 0.0]
    v_hat_I, v_hat_B, M_B, wdot_B = rotational_rhs_from_state(y, rp)
    assert M_B[2] > 0.0, f"Expected Mz>0 for positive v_hat_By, got {M_B}"
    assert math.isfinite(float(wdot_B[2]))


def _check_no_nan_inf_typical_params():
    rp = RotParams(Ix=0.02, Iy=0.10, Iz=0.12, k_stab=5.0)
    y = np.zeros(18, dtype=float)
    y[IDX_A0:IDX_A0 + 9] = np.eye(3).reshape(9)
    y[IDX_VX:IDX_VZ + 1] = [800.0, -120.0, 50.0]
    y[IDX_WX:IDX_WZ + 1] = [200.0, 10.0, -5.0]
    _, _, _, wdot_B = rotational_rhs_from_state(y, rp)
    assert np.all(np.isfinite(wdot_B)), f"Non-finite wdot: {wdot_B}"


if __name__ == "__main__":
    _check_aligned_gives_zero_moment()
    _check_crossflow_generates_moment()
    _check_no_nan_inf_typical_params()
    print("Step 6 checks: OK")
