"""
3D баллистика (траектория + 3 проекции) по твоей системе уравнений.
Метод: Эйлер (как в тексте), шаг dt постоянный.

Что строит:
1) x(z) — проекция XZ
2) y(z) — проекция YZ
3) x(y) — проекция XY
(и дополнительно 3D-траекторию)

Важно:
- Модель атмосферы ISA: 0–32 км (тропосфера, изотермический слой, стратосфера)
- Cd(M) — кусочная аппроксимация (как у тебя)
- Подъёмная сила: q S CL n_hat
- Магнус: CM rho S (omega x v)
- Стабилизирующий момент: M = k_stab (e_x_body x v_hat)
- Ориентация через направляющие косинусы alpha_ij + уравнения Пуассона
"""

from dataclasses import dataclass
import math
import numpy as np
import matplotlib.pyplot as plt


# ----------------------------
# Параметры модели
# ----------------------------
@dataclass
class Params:
    # Снаряд
    m: float = 46.0                 # кг
    S: float = 0.018                # м^2
    Ix: float = 0.12                # кг*м^2 (пример, задай свои)
    Iy: float = 0.90                # кг*м^2
    Iz: float = 0.90                # кг*м^2

    # Аэродинамика
    CL: float = 0.015
    CM: float = 2e-4                # как у тебя в примере
    k_stab: float = 0.6             # Н*м (эффективный коэффициент)

    # Атмосфера/физика
    g: float = 9.80665              # м/с^2
    gamma: float = 1.4
    R_air: float = 287.05           # Дж/(кг*K)

    # Численное интегрирование
    dt: float = 0.01                # с
    k_max: int = 100000
    v_stop: float = 0.1             # м/с


# ----------------------------
# ISA (0–32 км) + скорость звука
# ----------------------------
def isa_T_p(h: float, p: Params):
    """
    Возвращает (T, p_atm) для высоты h (м) по ISA, слой 0–32 км.
    """
    # Константы ISA
    T0 = 288.15       # K
    p0 = 101325.0     # Pa

    if h < 0.0:
        h = 0.0

    # 1) Тропосфера 0–11 км
    L1 = -0.0065
    if h <= 11000.0:
        T = T0 + L1 * h
        p_atm = p0 * (T / T0) ** (-p.g / (L1 * p.R_air))
        return T, p_atm

    # значения на 11 км
    T11 = T0 + L1 * 11000.0
    p11 = p0 * (T11 / T0) ** (-p.g / (L1 * p.R_air))

    # 2) Изотермический слой 11–20 км
    if h <= 20000.0:
        T = T11
        p_atm = p11 * math.exp(-p.g * (h - 11000.0) / (p.R_air * T11))
        return T, p_atm

    # значения на 20 км
    T20 = T11
    p20 = p11 * math.exp(-p.g * (20000.0 - 11000.0) / (p.R_air * T11))

    # 3) Стратосфера 20–32 км
    L3 = 0.001
    if h <= 32000.0:
        T = T20 + L3 * (h - 20000.0)
        p_atm = p20 * (T / T20) ** (-p.g / (L3 * p.R_air))
        return T, p_atm

    # Выше 32 км — оставим значения на 32 км (для устойчивости)
    T32 = T20 + L3 * (32000.0 - 20000.0)
    p32 = p20 * (T32 / T20) ** (-p.g / (L3 * p.R_air))
    return T32, p32


def rho_a(h: float, p: Params):
    T, p_atm = isa_T_p(h, p)
    rho = p_atm / (p.R_air * T)
    a = math.sqrt(p.gamma * p.R_air * T)
    return rho, a


# ----------------------------
# Cd(M) — как в твоём тексте
# ----------------------------
def Cd_of_M(M: float) -> float:
    if M < 0.8:
        return 0.20 + 0.10 * M * M
    if M < 1.2:
        d = (M - 1.0)
        return 0.45 + 0.30 * d * d
    if M < 3.0:
        return 0.30 + 0.10 / max(M, 1e-9)
    return 0.25


# ----------------------------
# Вспомогательная математика
# ----------------------------
def safe_unit(v: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n < eps:
        return np.zeros_like(v), 0.0
    return v / n, n


def n_hat_from(v: np.ndarray, e_x_body: np.ndarray) -> np.ndarray:
    """
    n_hat = (v x (e_x_body x v)) / ||...||
    """
    cross1 = np.cross(e_x_body, v)
    cross2 = np.cross(v, cross1)
    nh, nrm = safe_unit(cross2)
    # Если скорость почти параллельна продольной оси => nh не определён.
    # Тогда просто вернём 0 (подъёмная сила исчезнет).
    if nrm == 0.0:
        return np.zeros(3)
    return nh


# ----------------------------
# Одна итерация Эйлера по системе (40)
# ----------------------------
def step_euler(state: np.ndarray, prm: Params) -> np.ndarray:
    """
    state = [x,y,z, vx,vy,vz, wx,wy,wz, a11,a12,a13,a21,a22,a23,a31,a32,a33]
    """
    x, y, z = state[0:3]
    vx, vy, vz = state[3:6]
    wx, wy, wz = state[6:9]

    # матрица направляющих косинусов (строки = оси тела в инерциальной СК)
    a11, a12, a13 = state[9:12]
    a21, a22, a23 = state[12:15]
    a31, a32, a33 = state[15:18]

    v = np.array([vx, vy, vz], dtype=float)
    omega = np.array([wx, wy, wz], dtype=float)

    v_hat, V = safe_unit(v)
    rho, a_sound = rho_a(z, prm)
    M = V / max(a_sound, 1e-9)
    q = 0.5 * rho * V * V
    CD = Cd_of_M(M)

    # продольная ось корпуса (как в твоём описании e_x^body = [a11,a21,a31]^T)
    e_x_body = np.array([a11, a21, a31], dtype=float)

    # n_hat
    n_hat = n_hat_from(v, e_x_body)

    # Силы
    F_D = -q * prm.S * CD * v_hat
    F_L = q * prm.S * prm.CL * n_hat
    F_M = prm.CM * rho * prm.S * np.cross(omega, v)

    # Ускорение
    a = (F_D + F_L + F_M) / prm.m + np.array([0.0, 0.0, -prm.g], dtype=float)

    # Момент стабилизации
    M_ext = prm.k_stab * np.cross(e_x_body, v_hat)

    # Уравнения Эйлера:
    # Ix*wx_dot - (Iy-Iz)*wy*wz = Mx  => wx_dot = (Mx + (Iy-Iz)*wy*wz)/Ix
    # Iy*wy_dot - (Iz-Ix)*wz*wx = My  => wy_dot = (My + (Iz-Ix)*wz*wx)/Iy
    # Iz*wz_dot - (Ix-Iy)*wx*wy = Mz  => wz_dot = (Mz + (Ix-Iy)*wx*wy)/Iz
    Mx, My, Mz = M_ext
    wx_dot = (Mx + (prm.Iy - prm.Iz) * wy * wz) / prm.Ix
    wy_dot = (My + (prm.Iz - prm.Ix) * wz * wx) / prm.Iy
    wz_dot = (Mz + (prm.Ix - prm.Iy) * wx * wy) / prm.Iz

    # Уравнения Пуассона для alpha_ij (как в твоей записи)
    # row1
    a11_dot = a12 * wz - a13 * wy
    a12_dot = a13 * wx - a11 * wz
    a13_dot = a11 * wy - a12 * wx
    # row2
    a21_dot = a22 * wz - a23 * wy
    a22_dot = a23 * wx - a21 * wz
    a23_dot = a21 * wy - a22 * wx
    # row3
    a31_dot = a32 * wz - a33 * wy
    a32_dot = a33 * wx - a31 * wz
    a33_dot = a31 * wy - a32 * wx

    # Собираем производные
    d = np.zeros_like(state)

    d[0:3] = v
    d[3:6] = a
    d[6:9] = np.array([wx_dot, wy_dot, wz_dot], dtype=float)

    d[9:12] = np.array([a11_dot, a12_dot, a13_dot], dtype=float)
    d[12:15] = np.array([a21_dot, a22_dot, a23_dot], dtype=float)
    d[15:18] = np.array([a31_dot, a32_dot, a33_dot], dtype=float)

    # Шаг Эйлера
    return state + prm.dt * d


# ----------------------------
# Нормализация alpha_ij (чтобы не "уплывала" ортонормальность)
# В твоём тексте это делалось через экспоненту, но ты просил Эйлер.
# Здесь — мягкая подстраховка: ортонормируем строки (оси тела) методом Грама–Шмидта.
# Если хочешь "чисто по тексту" без этого — скажи, я уберу.
# ----------------------------
def re_orthonormalize_alpha(state: np.ndarray) -> np.ndarray:
    A = np.array([
        state[9:12],
        state[12:15],
        state[15:18]
    ], dtype=float)

    # Грам–Шмидт по строкам
    r1 = A[0]
    r1, _ = safe_unit(r1)

    r2 = A[1] - np.dot(A[1], r1) * r1
    r2, n2 = safe_unit(r2)
    if n2 == 0.0:
        # если деградация — восстановим перпендикуляр через векторное произведение
        r2 = np.cross(np.array([0.0, 0.0, 1.0]), r1)
        r2, _ = safe_unit(r2)

    r3 = np.cross(r1, r2)
    r3, _ = safe_unit(r3)

    state[9:12] = r1
    state[12:15] = r2
    state[15:18] = r3
    return state


# ----------------------------
# Симуляция
# ----------------------------
def simulate(prm: Params,
             v0: float,
             theta_deg: float,
             psi_deg: float,
             h0: float,
             omega0_body: tuple[float, float, float]):
    theta = math.radians(theta_deg)
    psi = math.radians(psi_deg)

    # Начальные условия как у тебя
    x0, y0, z0 = 0.0, 0.0, h0
    vx0 = v0 * math.cos(theta) * math.cos(psi)
    vy0 = v0 * math.cos(theta) * math.sin(psi)
    vz0 = v0 * math.sin(theta)

    wx0, wy0, wz0 = omega0_body

    # alpha_ij(0) = delta_ij
    state = np.array([
        x0, y0, z0,
        vx0, vy0, vz0,
        wx0, wy0, wz0,
        1.0, 0.0, 0.0,   # row1
        0.0, 1.0, 0.0,   # row2
        0.0, 0.0, 1.0    # row3
    ], dtype=float)

    traj = []
    t = 0.0

    for k in range(prm.k_max):
        traj.append((t, *state[0:6]))  # t, x,y,z,vx,vy,vz

        v = state[3:6]
        V = float(np.linalg.norm(v))

        # стоп-условия
        if state[2] <= 0.0 and k > 5:
            break
        if V <= prm.v_stop:
            break

        state = step_euler(state, prm)
        state = re_orthonormalize_alpha(state)  # можно отключить при желании

        t += prm.dt

    return np.array(traj, dtype=float)


# ----------------------------
# Графики
# ----------------------------
def plot_trajectory(traj: np.ndarray):
    # traj columns: t, x,y,z,vx,vy,vz
    x = traj[:, 1]
    y = traj[:, 2]
    z = traj[:, 3]

    # 3D
    fig = plt.figure()
    ax = fig.add_subplot(111, projection='3d')
    ax.plot(x, y, z)
    ax.set_xlabel("x, м")
    ax.set_ylabel("y, м")
    ax.set_zlabel("z, м")
    ax.set_title("3D траектория")

    # XZ
    plt.figure()
    plt.plot(x, z)
    plt.xlabel("x, м")
    plt.ylabel("z, м")
    plt.title("Проекция XZ (z(x))")
    plt.grid(True)

    # YZ
    plt.figure()
    plt.plot(y, z)
    plt.xlabel("y, м")
    plt.ylabel("z, м")
    plt.title("Проекция YZ (z(y))")
    plt.grid(True)

    # XY
    plt.figure()
    plt.plot(x, y)
    plt.xlabel("x, м")
    plt.ylabel("y, м")
    plt.title("Проекция XY (y(x))")
    plt.grid(True)

    plt.show()


# ----------------------------
# Пример запуска (замени параметры под себя)
# ----------------------------
if __name__ == "__main__":
    prm = Params(
        m=46.0,
        S=0.018,
        Ix=0.12, Iy=0.90, Iz=0.90,   # <-- поставь свои
        CL=0.015,
        CM=2e-4,
        k_stab=0.6,
        dt=0.01
    )

    traj = simulate(
        prm=prm,
        v0=820.0,
        theta_deg=45.0,
        psi_deg=0.0,
        h0=0.0,
        omega0_body=(300.0, 0.0, 0.0)  # рад/с в осях корпуса
    )

    print(f"steps={len(traj)}  flight_time≈{traj[-1,0]:.2f} s  range≈{traj[-1,1]:.1f} m")
    plot_trajectory(traj)
