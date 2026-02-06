# aerodynamics.py
"""
Step 3: Aerodynamics helpers
- V = |v|
- v_hat = v / V with safe rule at small V
- Mach M = V / a(h)
- dynamic pressure q = 0.5 * rho(h) * V^2
- drag coefficient C_D(M) piecewise (per prompt), with safe rule for M->0 on 0.10/M branch

Depends on Step 2 ISA implementation: isa_rho_a(h).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import math
import numpy as np

from atmosphere_isa import isa_rho_a


# ----------------- small-number policies -----------------
@dataclass(frozen=True)
class AeroEps:
    v_eps: float = 1e-9   # m/s, to avoid division by 0 in v_hat
    m_eps: float = 1e-6   # Mach, to avoid 0.10/M blowups


EPS = AeroEps()


# ----------------- core helpers -----------------
def speed_and_vhat(v: np.ndarray, eps: AeroEps = EPS) -> Tuple[float, np.ndarray]:
    """
    Args:
      v: (3,) inertial velocity vector [m/s]

    Returns:
      V: speed [m/s]
      v_hat: unit direction (0 vector if V < eps.v_eps)
    """
    vx, vy, vz = float(v[0]), float(v[1]), float(v[2])
    V = math.sqrt(vx * vx + vy * vy + vz * vz)

    if V < eps.v_eps:
        return V, np.zeros(3, dtype=float)

    return V, np.array([vx / V, vy / V, vz / V], dtype=float)


def mach_q_from_state(v: np.ndarray, h_m: float, eps: AeroEps = EPS) -> Tuple[float, float, float, float]:
    """
    Computes rho(h), a(h), Mach, dynamic pressure.
    Args:
      v: (3,) inertial velocity [m/s]
      h_m: altitude [m]

    Returns:
      rho [kg/m^3], a [m/s], M [-], q [Pa]
    """
    rho, a = isa_rho_a(h_m)
    V, _ = speed_and_vhat(v, eps=eps)
    # a(h) is always >0 from ISA, but keep safe anyway
    if a <= 0.0 or not math.isfinite(a):
        raise ValueError(f"Invalid speed of sound a(h)={a} at h={h_m}")
    M = V / a
    q = 0.5 * rho * V * V
    return rho, a, M, q


def C_D(M: float, eps: AeroEps = EPS) -> float:
    """
    Piecewise drag coefficient per prompt:

      M < 0.8           : 0.20 + 0.10 M^2
      0.8 <= M < 1.2    : 0.45 + 0.30 (M - 1)^2
      1.2 <= M < 3      : 0.30 + 0.10 / M
      M >= 3            : 0.25

    Safe rule:
      On 0.10/M branch, clamp M >= eps.m_eps
    """
    m = float(M)
    if m < 0.8:
        return 0.20 + 0.10 * (m * m)
    if m < 1.2:
        dm = (m - 1.0)
        return 0.45 + 0.30 * (dm * dm)
    if m < 3.0:
        m_safe = max(m, eps.m_eps)
        return 0.30 + 0.10 / m_safe
    return 0.25


# ----------------- Step-3 sanity checks -----------------
def _check_cd_continuity() -> None:
    # Check that it doesn't blow up at boundaries, and is finite.
    # We don't require equality; just "reasonable jump" and no inf/nan.
    for b in [0.8, 1.2, 3.0]:
        left = C_D(b - 1e-8)
        right = C_D(b + 1e-8)
        assert math.isfinite(left) and math.isfinite(right), f"C_D not finite near {b}"
        # allow a modest jump; this piecewise model isn't guaranteed continuous
        assert abs(right - left) < 1.0, f"C_D jump too large near {b}: left={left}, right={right}"


def _check_v_zero_safe() -> None:
    V, vhat = speed_and_vhat(np.array([0.0, 0.0, 0.0]))
    assert V == 0.0, "V should be exactly 0 for zero vector"
    assert np.allclose(vhat, 0.0), "v_hat should be zero vector when V is tiny"

    rho, a, M, q = mach_q_from_state(np.array([0.0, 0.0, 0.0]), h_m=0.0)
    assert M == 0.0, "Mach should be 0 at V=0"
    assert q == 0.0, "dynamic pressure should be 0 at V=0"
    assert rho > 0.0 and a > 0.0


if __name__ == "__main__":
    _check_cd_continuity()
    _check_v_zero_safe()

    # quick printout for a few Mach values
    for m in [0.0, 0.3, 0.8, 1.0, 1.2, 2.0, 3.0, 5.0]:
        print(f"M={m:4.1f} -> C_D={C_D(m):.4f}")

    # quick integrated check
    v = np.array([300.0, 0.0, 0.0])
    rho, a, M, q = mach_q_from_state(v, h_m=0.0)
    print(f"At sea level, V=300 m/s: rho={rho:.3f}, a={a:.1f}, M={M:.3f}, q={q:.1f} Pa")

    print("Step 3 checks: OK")
