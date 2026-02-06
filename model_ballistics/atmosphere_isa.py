# atmosphere_isa.py
"""
Step 2: Add ISA atmosphere (0–32 km) -> rho(h), a(h)

We implement International Standard Atmosphere (ISA) up to 32 km with 3 layers:
- 0     .. 11 km  : lapse rate L = -0.0065 K/m
- 11 km .. 20 km  : isothermal (L = 0)
- 20 km .. 32 km  : lapse rate L = +0.0010 K/m

We clamp h to [0, 32000] as per model requirement.

Returns:
- rho(h) [kg/m^3]
- a(h)   [m/s]
Optionally also T(h), p(h) for diagnostics.

This file is standalone for Step 2; later we can merge back into the main simulator file.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import math


# ---------- ISA constants ----------
G0 = 9.80665            # m/s^2 (standard gravity)
R_AIR = 287.05287       # J/(kg*K) specific gas constant for dry air
GAMMA = 1.4             # heat capacity ratio for air

# Sea level ISA conditions
T0 = 288.15             # K
P0 = 101325.0           # Pa

# Layer boundaries (m)
H1 = 11000.0
H2 = 20000.0
H3 = 32000.0

# Lapse rates (K/m)
L0 = -0.0065            # 0-11 km
L1 = 0.0                # 11-20 km (isothermal)
L2 = +0.0010            # 20-32 km


def _clamp_h(h: float) -> float:
    if h < 0.0:
        return 0.0
    if h > H3:
        return H3
    return h


def isa_T_p_rho_a(h_m: float) -> Tuple[float, float, float, float]:
    """
    ISA model for 0..32 km (clamped).

    Args:
      h_m: geometric altitude in meters

    Returns:
      T [K], p [Pa], rho [kg/m^3], a [m/s]
    """
    h = _clamp_h(float(h_m))

    # Precompute base values at layer boundaries
    # Layer 0 base (sea level)
    T_b0 = T0
    p_b0 = P0

    # At 11 km (end of layer 0)
    # For nonzero lapse rate: T = T_b + L*(h-h_b)
    # p = p_b * (T/T_b)^(-g0/(R*L))
    T_11 = T_b0 + L0 * (H1 - 0.0)
    p_11 = p_b0 * (T_11 / T_b0) ** (-G0 / (R_AIR * L0))

    # At 20 km (end of isothermal layer 1)
    T_20 = T_11  # isothermal
    # For isothermal: p = p_b * exp(-g0*(h-h_b)/(R*T))
    p_20 = p_11 * math.exp(-G0 * (H2 - H1) / (R_AIR * T_20))

    # Now compute for requested h
    if h <= H1:
        # Layer 0: lapse L0
        T = T_b0 + L0 * (h - 0.0)
        p = p_b0 * (T / T_b0) ** (-G0 / (R_AIR * L0))
    elif h <= H2:
        # Layer 1: isothermal
        T = T_20
        p = p_11 * math.exp(-G0 * (h - H1) / (R_AIR * T))
    else:
        # Layer 2: lapse L2, base at 20 km
        T = T_20 + L2 * (h - H2)
        p = p_20 * (T / T_20) ** (-G0 / (R_AIR * L2))

    rho = p / (R_AIR * T)
    a = math.sqrt(GAMMA * R_AIR * T)
    return T, p, rho, a


def isa_rho_a(h_m: float) -> Tuple[float, float]:
    """Convenience: return rho(h), a(h) only."""
    T, p, rho, a = isa_T_p_rho_a(h_m)
    return rho, a


# ---------- Step-2 sanity checks ----------
if __name__ == "__main__":
    for hm in [0.0, 10000.0, 11000.0, 20000.0, 32000.0]:
        T, p, rho, a = isa_T_p_rho_a(hm)
        print(f"h={hm:7.0f} m  T={T:7.2f} K  p={p:10.2f} Pa  rho={rho:8.4f} kg/m^3  a={a:7.2f} m/s")

    # Required sanity points:
    T0_, p0_, rho0_, a0_ = isa_T_p_rho_a(0.0)
    assert 1.0 < rho0_ < 1.4, f"rho(0) out of expected range: {rho0_}"
    assert 330.0 < a0_ < 350.0, f"a(0) out of expected range: {a0_}"

    T10_, p10_, rho10_, a10_ = isa_T_p_rho_a(10000.0)
    assert rho10_ < rho0_, "rho should decrease with altitude (0 -> 10 km)"
    assert 280.0 < a10_ < 320.0, f"a(10km) out of expected range: {a10_}"

    # Positivity / NaN safety
    for hm in [-100.0, 0.0, 5000.0, 15000.0, 25000.0, 40000.0]:
        rho, a = isa_rho_a(hm)
        assert rho > 0.0 and math.isfinite(rho), f"rho invalid at h={hm}: {rho}"
        assert a > 0.0 and math.isfinite(a), f"a invalid at h={hm}: {a}"

    print("ISA sanity checks: OK")
