# orientation_kinematics.py
"""
Step 7: Poisson equations for alpha (body->inertial) + orthonormalization

Contract (variant B):
- alpha is 3x3 rotation matrix body->inertial
- omega^B = (wx, wy, wz) is angular velocity in BODY frame
- Poisson kinematics (as in prompt) corresponds to:
    d(alpha)/dt = alpha * [omega]_x
  where [omega]_x is the skew-symmetric cross-product matrix built from omega^B.

We integrate alpha with Euler later; here we provide:
- alpha_dot_poisson(alpha, omega_B) -> alpha_dot
- orthonormalize_alpha_columns(alpha) -> alpha_ortho (Gram–Schmidt + det fix)
- diagnostics: ortho_error_norm, det(alpha)

Includes control checks:
- alpha^T alpha ≈ I and det ≈ +1 after repeated steps with ortho
- e_x_body (first column) remains unit length
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import math
import numpy as np


def skew_omega(omega_B: np.ndarray) -> np.ndarray:
    """Skew-symmetric matrix [omega]_x such that [omega]_x * u = omega x u."""
    wx, wy, wz = float(omega_B[0]), float(omega_B[1]), float(omega_B[2])
    return np.array([
        [0.0, -wz,  wy],
        [wz,  0.0, -wx],
        [-wy, wx,  0.0],
    ], dtype=float)


def alpha_dot_poisson(alpha: np.ndarray, omega_B: np.ndarray) -> np.ndarray:
    """
    Poisson equation for body->inertial DCM:
      dA/dt = A * [omega]_x
    omega is in BODY coordinates.
    """
    A = np.asarray(alpha, dtype=float).reshape(3, 3)
    W = skew_omega(np.asarray(omega_B, dtype=float).reshape(3,))
    dA = A @ W
    return dA


def _safe_unit(v: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if not math.isfinite(n) or n < eps:
        raise FloatingPointError("Cannot normalize near-zero or non-finite vector in orthonormalization.")
    return v / n


def orthonormalize_alpha_columns(alpha: np.ndarray) -> np.ndarray:
    """
    Orthonormalize columns of alpha via Gram–Schmidt:
      c1 = normalize(c1)
      c2 = normalize(c2 - (c1·c2)c1)
      c3 = c1 x c2  (ensures right-handedness and orthogonality)

    Then fix det to +1 (c3 constructed this way should already yield det>0,
    but we still guard).
    """
    A = np.asarray(alpha, dtype=float).reshape(3, 3)

    c1 = A[:, 0].copy()
    c2 = A[:, 1].copy()

    c1 = _safe_unit(c1)
    c2 = c2 - float(np.dot(c1, c2)) * c1
    c2 = _safe_unit(c2)

    c3 = np.cross(c1, c2)
    c3 = _safe_unit(c3)

    A_ortho = np.column_stack((c1, c2, c3))

    # det fix (should be +1, but just in case numerical weirdness flips it)
    detA = float(np.linalg.det(A_ortho))
    if detA < 0.0:
        # flip third column to restore right-handedness
        A_ortho[:, 2] *= -1.0

    return A_ortho


def ortho_error_norm(alpha: np.ndarray) -> float:
    """Return Frobenius norm of (A^T A - I)."""
    A = np.asarray(alpha, dtype=float).reshape(3, 3)
    E = A.T @ A - np.eye(3)
    return float(np.linalg.norm(E, ord="fro"))


def ex_body_from_alpha(alpha: np.ndarray) -> np.ndarray:
    """First column: body x-axis expressed in inertial frame."""
    A = np.asarray(alpha, dtype=float).reshape(3, 3)
    return A[:, 0].copy()


# ----------------- Step-7 control checks -----------------
def _check_orthonormalization_properties():
    # Start with a slightly perturbed rotation-like matrix
    A = np.eye(3)
    A[0, 1] = 1e-3
    A[1, 2] = -2e-3
    A[2, 0] = 5e-4

    A2 = orthonormalize_alpha_columns(A)
    err = ortho_error_norm(A2)
    detA = float(np.linalg.det(A2))
    ex = ex_body_from_alpha(A2)
    ex_norm = float(np.linalg.norm(ex))

    assert err < 1e-10, f"Orthonormalization failed: ortho error={err}"
    assert abs(detA - 1.0) < 1e-10, f"det not +1: det={detA}"
    assert abs(ex_norm - 1.0) < 1e-12, f"e_x not unit: |ex|={ex_norm}"


def _check_does_not_drift_with_repeated_steps():
    # Integrate alpha with Euler for many steps and re-orthonormalize each step.
    dt = 1e-3
    steps = 5000
    omega_B = np.array([30.0, -10.0, 5.0])  # rad/s (big, to stress it)

    A = np.eye(3)
    max_err = 0.0
    for _ in range(steps):
        A = A + dt * alpha_dot_poisson(A, omega_B)
        A = orthonormalize_alpha_columns(A)
        max_err = max(max_err, ortho_error_norm(A))

    detA = float(np.linalg.det(A))
    ex = ex_body_from_alpha(A)
    assert max_err < 1e-8, f"Ortho error grew too large: {max_err}"
    assert abs(detA - 1.0) < 1e-8, f"det drifted: det={detA}"
    assert abs(float(np.linalg.norm(ex)) - 1.0) < 1e-10, "e_x drifted from unit length"


if __name__ == "__main__":
    _check_orthonormalization_properties()
    _check_does_not_drift_with_repeated_steps()
    print("Step 7 checks: OK")
