# visualization.py
"""
Построение графиков по результатам расчёта (БЕЗ расчёта).

Ожидает, что расчёт уже выполнен (например, через run_vkr.py) и в папке out есть:
  - trajectory.csv
  - diagnostics.csv

Сохраняет PNG в:
  out/plots/*.png

Usage:
  python visualization.py --out outputs
"""

from __future__ import annotations

import argparse
import os
from typing import Dict, Any

import numpy as np
import matplotlib.pyplot as plt


# ----------------- IO helpers -----------------
def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def load_csv_struct(path: str) -> np.ndarray:
    """
    Читает CSV с заголовком (header) и возвращает structured array (names=True).
    """
    return np.genfromtxt(
        path,
        delimiter=",",
        names=True,
        dtype=None,
        encoding="utf-8",
    )


def load_trajectory_csv(path: str) -> tuple[np.ndarray, np.ndarray]:
    """
    trajectory.csv columns:
      t, X,Y,Z, Vx,Vy,Vz, wx,wy,wz, a11..a33
    Возвращает:
      t: (N,)
      Y: (N,18) state matrix в порядке как в проекте.
    """
    arr = load_csv_struct(path)

    t = np.asarray(arr["t"], dtype=float)

    state_fields = [
        "X", "Y", "Z",
        "Vx", "Vy", "Vz",
        "wx", "wy", "wz",
        "a11", "a12", "a13",
        "a21", "a22", "a23",
        "a31", "a32", "a33",
    ]

    # Проверим, что все поля на месте (чтобы не получить молча битые графики)
    missing = [f for f in state_fields if f not in arr.dtype.names]
    if missing:
        raise ValueError(
            f"trajectory.csv: отсутствуют поля {missing}. "
            f"Найденные поля: {list(arr.dtype.names)}"
        )

    Y = np.column_stack([np.asarray(arr[f], dtype=float) for f in state_fields])
    return t, Y


def load_diagnostics_csv(path: str) -> Dict[str, np.ndarray]:
    """
    diagnostics.csv columns:
      t, V, M, C_D, q, rho, a, n_norm, n_dot_vhat, detA, ortho_err, vhatBy_abs, vhatBz_abs

    Возвращает dict с ключами, которые ждёт save_plots():
      V, M, Cd, q, n_norm, n_dot_vhat, detA, ortho_err, vhatBy_abs, vhatBz_abs
      (+ rho, a — тоже сохраняем, вдруг пригодится)
    """
    arr = load_csv_struct(path)

    # Маппинг имён из CSV -> имена, используемые в старом коде графиков
    mapping = {
        "V": "V",
        "M": "M",
        "C_D": "Cd",          # важно: в графиках используется diag["Cd"]
        "q": "q",
        "rho": "rho",
        "a": "a",
        "n_norm": "n_norm",
        "n_dot_vhat": "n_dot_vhat",
        "detA": "detA",
        "ortho_err": "ortho_err",
        "vhatBy_abs": "vhatBy_abs",
        "vhatBz_abs": "vhatBz_abs",
    }

    missing = [src for src in mapping.keys() if src not in arr.dtype.names]
    if missing:
        raise ValueError(
            f"diagnostics.csv: отсутствуют поля {missing}. "
            f"Найденные поля: {list(arr.dtype.names)}"
        )

    diag: Dict[str, np.ndarray] = {}
    for src, dst in mapping.items():
        diag[dst] = np.asarray(arr[src], dtype=float)

    return diag


# ----------------- plotting (save to PNG, no GUI) -----------------
def save_plots(out_dir: str, t: np.ndarray, Y: np.ndarray, diag: dict) -> None:
    os.makedirs(out_dir, exist_ok=True)

    X = Y[:, 0]
    Yp = Y[:, 1]
    Z = Y[:, 2]

    def save(fig, name):
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, name), dpi=200)
        plt.close(fig)

    # ==========================================================
    # 1. Проекции траектории
    # ==========================================================
    fig, axs = plt.subplots(2, 2, figsize=(10, 8))

    axs[0, 0].plot(X, Z)
    axs[0, 0].set_title("Z(X)")
    axs[0, 0].set_xlabel("X, м")
    axs[0, 0].set_ylabel("Z, м")
    axs[0, 0].grid()

    axs[0, 1].plot(Yp, Z)
    axs[0, 1].set_title("Z(Y)")
    axs[0, 1].set_xlabel("Y, м")
    axs[0, 1].set_ylabel("Z, м")
    axs[0, 1].grid()

    axs[1, 0].plot(X, Yp)
    axs[1, 0].set_title("Y(X)")
    axs[1, 0].set_xlabel("X, м")
    axs[1, 0].set_ylabel("Y, м")
    axs[1, 0].grid()

    ax3d = fig.add_subplot(2, 2, 4, projection="3d")
    ax3d.plot(X, Yp, Z)
    ax3d.set_title("Пространственная траектория")
    ax3d.set_xlabel("X, м")
    ax3d.set_ylabel("Y, м")
    ax3d.set_zlabel("Z, м")

    fig.suptitle("Проекции траектории полёта снаряда")
    save(fig, "fig_trajectory.png")

    # ==========================================================
    # 2. Кинематика и аэродинамика
    # ==========================================================
    fig, axs = plt.subplots(2, 2, figsize=(10, 8))

    axs[0, 0].plot(t, diag["V"])
    axs[0, 0].set_title("Модуль скорости V(t)")
    axs[0, 0].set_xlabel("t, с")
    axs[0, 0].set_ylabel("V, м/с")
    axs[0, 0].grid()

    axs[0, 1].plot(t, diag["M"])
    axs[0, 1].set_title("Число Маха M(t)")
    axs[0, 1].set_xlabel("t, с")
    axs[0, 1].set_ylabel("M, –")
    axs[0, 1].grid()

    axs[1, 0].plot(t, diag["Cd"])
    axs[1, 0].set_title("Коэффициент сопротивления C_D(t)")
    axs[1, 0].set_xlabel("t, с")
    axs[1, 0].set_ylabel("C_D, –")
    axs[1, 0].grid()

    axs[1, 1].plot(t, diag["q"])
    axs[1, 1].set_title("Динамическое давление q(t)")
    axs[1, 1].set_xlabel("t, с")
    axs[1, 1].set_ylabel("q, Па")
    axs[1, 1].grid()

    fig.suptitle("Кинематические и аэродинамические параметры")
    save(fig, "fig_kinematics.png")

    # ==========================================================
    # 3. Геометрия подъёмной силы и корректность матрицы ориентации
    # ==========================================================
    fig, axs = plt.subplots(2, 2, figsize=(10, 8))

    axs[0, 0].plot(t, diag["n_norm"])
    axs[0, 0].set_title("Норма вектора n̂")
    axs[0, 0].set_xlabel("t, с")
    axs[0, 0].set_ylabel("|n̂|, –")
    axs[0, 0].grid()

    axs[0, 1].plot(t, diag["n_dot_vhat"])
    axs[0, 1].set_title("Скалярное произведение n̂ · v̂")
    axs[0, 1].set_xlabel("t, с")
    axs[0, 1].set_ylabel("–")
    axs[0, 1].grid()

    axs[1, 0].plot(t, diag["detA"])
    axs[1, 0].set_title("Определитель матрицы α")
    axs[1, 0].set_xlabel("t, с")
    axs[1, 0].set_ylabel("det(α), –")
    axs[1, 0].grid()

    axs[1, 1].plot(t, diag["ortho_err"])
    axs[1, 1].set_title("Ортонормальность ||αᵀα − I||")
    axs[1, 1].set_xlabel("t, с")
    axs[1, 1].set_ylabel("–")
    axs[1, 1].grid()

    fig.suptitle("Корректность геометрии и ориентации")
    save(fig, "fig_rotation_consistency.png")

    # ==========================================================
    # 4. Направление скорости в системе координат тела
    # ==========================================================
    fig, axs = plt.subplots(2, 2, figsize=(10, 8))

    axs[0, 0].plot(t, diag["vhatBy_abs"])
    axs[0, 0].set_title("|v̂ᵇ_y|(t)")
    axs[0, 0].set_xlabel("t, с")
    axs[0, 0].set_ylabel("–")
    axs[0, 0].grid()

    axs[0, 1].plot(t, diag["vhatBz_abs"])
    axs[0, 1].set_title("|v̂ᵇ_z|(t)")
    axs[0, 1].set_xlabel("t, с")
    axs[0, 1].set_ylabel("–")
    axs[0, 1].grid()

    axs[1, 0].axis("off")
    axs[1, 1].axis("off")

    fig.suptitle("Ориентация вектора скорости в системе тела")
    save(fig, "fig_body_velocity.png")


# ----------------- main -----------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default="outputs",
        help="Каталог с результатами расчёта (по умолчанию: outputs)",
    )
    ap.add_argument(
        "--trajectory",
        default="trajectory.csv",
        help="Имя файла траектории внутри out (по умолчанию: trajectory.csv)",
    )
    ap.add_argument(
        "--diagnostics",
        default="diagnostics.csv",
        help="Имя файла диагностик внутри out (по умолчанию: diagnostics.csv)",
    )
    args = ap.parse_args()

    out_dir = args.out
    traj_path = os.path.join(out_dir, args.trajectory)
    diag_path = os.path.join(out_dir, args.diagnostics)

    if not os.path.isfile(traj_path):
        raise FileNotFoundError(f"Не найден файл траектории: {traj_path}")
    if not os.path.isfile(diag_path):
        raise FileNotFoundError(f"Не найден файл диагностик: {diag_path}")

    t, Y = load_trajectory_csv(traj_path)
    diag = load_diagnostics_csv(diag_path)

    plots_dir = os.path.join(out_dir, "plots")
    save_plots(plots_dir, t, Y, diag)

    print(f"Готово. Графики сохранены в: {plots_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
