# diagnostics.py
"""
Step 10: Plots + diagnostics

Build required trajectory plots:
  - Z(X), Z(Y), Y(X), 3D (X,Y,Z)

Add technical diagnostics plots:
  - V(t), M(t), C_D(t)
  - |n_hat|, n_hat · v_hat
  - det(alpha), ||alpha^T alpha - I||_F
  - |v_hat_B_y|, |v_hat_B_z|

Also prints control-check summaries:
  - max(| |n|-1 |), max(|n·vhat|)
  - det(alpha) min/max
  - C_D min/max
  - sanity notes
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, Optional, Tuple
import math
import numpy as np

from impact_event import simulate_euler_full_with_impact
from integrator_euler import (
    IDX_X, IDX_Y, IDX_Z, IDX_VX, IDX_VY, IDX_VZ, IDX_WX, IDX_WY, IDX_WZ,
    alpha_from_state, make_initial_state, SimParams, FullParams
)
from translational_dynamics import PhysParams, accel_inertial_from_state
from rotational_dynamics import RotParams, vhat_inertial_and_body
from aerodynamics import mach_q_from_state, C_D, speed_and_vhat, EPS as AERO_EPS
from lift_geometry import compute_n_hat
from orientation_kinematics import ortho_error_norm


def compute_diagnostics(
    t: np.ndarray,
    Y: np.ndarray,
    P: FullParams,
) -> Dict[str, np.ndarray]:
    """
    Recompute diagnostics along an already simulated trajectory.
    Uses prev_n_hat memory in the same spirit as the integrator to avoid 0/0 issues.

    Returns dict of arrays aligned with t (len N):
      V, M, Cd, q, rho, a
      n_norm, n_dot_vhat
      detA, ortho_err
      vhatBy_abs, vhatBz_abs
    """
    N = len(t)
    V_arr = np.zeros(N)
    M_arr = np.zeros(N)
    Cd_arr = np.zeros(N)
    q_arr = np.zeros(N)
    rho_arr = np.zeros(N)
    a_arr = np.zeros(N)

    n_norm = np.zeros(N)
    n_dot_vhat = np.zeros(N)

    detA = np.zeros(N)
    ortho_err = np.zeros(N)

    vhatBy_abs = np.zeros(N)
    vhatBz_abs = np.zeros(N)

    prev_n_hat: Optional[np.ndarray] = None

    for i in range(N):
        y = Y[i]
        Z = float(y[IDX_Z])

        v_I = np.array([y[IDX_VX], y[IDX_VY], y[IDX_VZ]], dtype=float)
        A = alpha_from_state(y)

        # V and vhat
        V, v_hat_I = speed_and_vhat(v_I, eps=AERO_EPS)
        V_arr[i] = V

        # atmosphere + Mach + q
        rho, a, M, q = mach_q_from_state(v_I, h_m=Z, eps=AERO_EPS)
        rho_arr[i] = rho
        a_arr[i] = a
        M_arr[i] = M
        q_arr[i] = q

        # Cd
        cd = C_D(M, eps=AERO_EPS)
        Cd_arr[i] = cd

        # n_hat (use same safe logic; prefer the same prev_n_hat mechanism)
        n_hat, _ = compute_n_hat(v_I, A, prev_n_hat=prev_n_hat)
        prev_n_hat = n_hat

        n_norm[i] = float(np.linalg.norm(n_hat))
        n_dot_vhat[i] = float(np.dot(n_hat, v_hat_I))

        # alpha diagnostics
        detA[i] = float(np.linalg.det(A))
        ortho_err[i] = float(ortho_error_norm(A))

        # v_hat^B diagnostics
        _, v_hat_B, _ = vhat_inertial_and_body(v_I, A)
        vhatBy_abs[i] = abs(float(v_hat_B[1]))
        vhatBz_abs[i] = abs(float(v_hat_B[2]))

    return {
        "V": V_arr,
        "M": M_arr,
        "Cd": Cd_arr,
        "q": q_arr,
        "rho": rho_arr,
        "a": a_arr,
        "n_norm": n_norm,
        "n_dot_vhat": n_dot_vhat,
        "detA": detA,
        "ortho_err": ortho_err,
        "vhatBy_abs": vhatBy_abs,
        "vhatBz_abs": vhatBz_abs,
    }


def print_control_checks(diag: Dict[str, np.ndarray]) -> None:
    n_norm = diag["n_norm"]
    n_dot = diag["n_dot_vhat"]
    detA = diag["detA"]
    ortho = diag["ortho_err"]
    Cd = diag["Cd"]

    max_n_norm_err = float(np.max(np.abs(n_norm - 1.0)))
    max_n_dot = float(np.max(np.abs(n_dot)))

    det_min = float(np.min(detA))
    det_max = float(np.max(detA))
    ortho_max = float(np.max(ortho))

    cd_min = float(np.min(Cd))
    cd_max = float(np.max(Cd))

    print("\n=== Step 10 Control Checks ===")
    print(f"max(| |n|-1 |)        : {max_n_norm_err:.3e}")
    print(f"max(| n·v_hat |)      : {max_n_dot:.3e}")
    print(f"det(alpha) min/max    : {det_min:.6f} / {det_max:.6f}")
    print(f"max(||A^T A - I||_F)  : {ortho_max:.3e}")
    print(f"C_D min/max           : {cd_min:.3f} / {cd_max:.3f}")
    if cd_min < 0.0 or cd_max > 2.0:
        print("WARNING: C_D out of a very broad sanity range; check Mach regime / formula.")
    print("================================\n")


def plot_required_and_tech(t: np.ndarray, Y: np.ndarray, diag: Dict[str, np.ndarray]) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception as ex:
        raise RuntimeError("matplotlib is required for plotting") from ex

    X = Y[:, IDX_X]
    Yp = Y[:, IDX_Y]
    Z = Y[:, IDX_Z]

    # ==========================================================
    # Figure 1 — Trajectory projections
    # ==========================================================
    fig, axs = plt.subplots(2, 2, figsize=(10, 8))

    axs[0, 0].plot(X, Z)
    axs[0, 0].set_title("Z(X)")
    axs[0, 0].set_xlabel("X, m")
    axs[0, 0].set_ylabel("Z, m")
    axs[0, 0].grid(True)

    axs[0, 1].plot(Yp, Z)
    axs[0, 1].set_title("Z(Y)")
    axs[0, 1].set_xlabel("Y, m")
    axs[0, 1].set_ylabel("Z, m")
    axs[0, 1].grid(True)

    axs[1, 0].plot(X, Yp)
    axs[1, 0].set_title("Y(X)")
    axs[1, 0].set_xlabel("X, m")
    axs[1, 0].set_ylabel("Y, m")
    axs[1, 0].grid(True)

    ax3d = fig.add_subplot(2, 2, 4, projection="3d")
    ax3d.plot(X, Yp, Z)
    ax3d.set_title("3D trajectory")
    ax3d.set_xlabel("X, m")
    ax3d.set_ylabel("Y, m")
    ax3d.set_zlabel("Z, m")

    fig.suptitle("Trajectory projections", fontsize=14)
    fig.tight_layout()

    # ==========================================================
    # Figure 2 — Kinematics
    # ==========================================================
    fig, axs = plt.subplots(2, 2, figsize=(10, 8))

    axs[0, 0].plot(t, diag["V"])
    axs[0, 0].set_title("Speed V(t)")
    axs[0, 0].set_xlabel("t, s")
    axs[0, 0].set_ylabel("V, m/s")
    axs[0, 0].grid(True)

    axs[0, 1].plot(t, diag["M"])
    axs[0, 1].set_title("Mach M(t)")
    axs[0, 1].set_xlabel("t, s")
    axs[0, 1].set_ylabel("M")
    axs[0, 1].grid(True)

    axs[1, 0].plot(t, diag["Cd"])
    axs[1, 0].set_title("C_D(t)")
    axs[1, 0].set_xlabel("t, s")
    axs[1, 0].set_ylabel("C_D")
    axs[1, 0].grid(True)

    axs[1, 1].plot(t, diag["q"])
    axs[1, 1].set_title("Dynamic pressure q(t)")
    axs[1, 1].set_xlabel("t, s")
    axs[1, 1].set_ylabel("q, Pa")
    axs[1, 1].grid(True)

    fig.suptitle("Kinematic and aerodynamic quantities", fontsize=14)
    fig.tight_layout()

    # ==========================================================
    # Figure 3 — Geometry & rotation validity
    # ==========================================================
    fig, axs = plt.subplots(2, 2, figsize=(10, 8))

    axs[0, 0].plot(t, diag["n_norm"])
    axs[0, 0].set_title("|n̂|(t)")
    axs[0, 0].set_xlabel("t, s")
    axs[0, 0].grid(True)

    axs[0, 1].plot(t, diag["n_dot_vhat"])
    axs[0, 1].set_title("n̂ · v̂")
    axs[0, 1].set_xlabel("t, s")
    axs[0, 1].grid(True)

    axs[1, 0].plot(t, diag["detA"])
    axs[1, 0].set_title("det(α)")
    axs[1, 0].set_xlabel("t, s")
    axs[1, 0].grid(True)

    axs[1, 1].plot(t, diag["ortho_err"])
    axs[1, 1].set_title("||αᵀα − I||")
    axs[1, 1].set_xlabel("t, s")
    axs[1, 1].grid(True)

    fig.suptitle("Rotation matrix consistency", fontsize=14)
    fig.tight_layout()

    # ==========================================================
    # Figure 4 — Velocity in body frame
    # ==========================================================
    fig, axs = plt.subplots(2, 2, figsize=(10, 8))

    axs[0, 0].plot(t, diag["vhatBy_abs"])
    axs[0, 0].set_title("|v̂ᵇ_y|(t)")
    axs[0, 0].set_xlabel("t, s")
    axs[0, 0].grid(True)

    axs[0, 1].plot(t, diag["vhatBz_abs"])
    axs[0, 1].set_title("|v̂ᵇ_z|(t)")
    axs[0, 1].set_xlabel("t, s")
    axs[0, 1].grid(True)

    axs[1, 0].axis("off")
    axs[1, 1].axis("off")

    fig.suptitle("Velocity direction in body frame", fontsize=14)
    fig.tight_layout()

    plt.show()



def main():
    # --- Example run parameters (replace with your real projectile params) ---
    phys = PhysParams(
        m=10.0,         # kg
        S=0.01,         # m^2
        C_L=0.0,        # set 0 initially to validate "no lift" physicality
        C_mp=0.0,       # set 0 initially
        g=9.81
    )
    rot = RotParams(
        Ix=0.02, Iy=0.10, Iz=0.10,
        k_stab=1.0
    )
    P = FullParams(phys=phys, rot=rot)

    # Example initial condition (replace with v0, theta, psi logic later if you want)
    y0 = make_initial_state(
        X=0.0, Y=0.0, Z=1.0,
        Vx=300.0, Vy=0.0, Vz=80.0,
        wx=0.0, wy=0.0, wz=100.0,
        alpha0=np.eye(3),
    )

    sim = SimParams(dt=0.002, t_max=120.0, max_steps=2_000_000)
    t, Y, info = simulate_euler_full_with_impact(y0, sim, P)

    print("Simulation info:", info)
    if info["reason"] == "ground":
        print(f"Impact at t={t[-1]:.3f} s, X={Y[-1,IDX_X]:.2f} m, Y={Y[-1,IDX_Y]:.2f} m, Z={Y[-1,IDX_Z]:.3f} m")

    diag = compute_diagnostics(t, Y, P)
    print_control_checks(diag)
    plot_required_and_tech(t, Y, diag)


if __name__ == "__main__":
    main()
