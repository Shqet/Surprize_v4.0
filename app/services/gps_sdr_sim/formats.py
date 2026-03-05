# app/services/gps_sdr_sim/formats.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, List

import csv
import math
import numpy as np


# ---------------------------
# WGS84 helpers
# ---------------------------

_WGS84_A = 6378137.0
_WGS84_F = 1.0 / 298.257223563
_WGS84_E2 = _WGS84_F * (2.0 - _WGS84_F)  # first eccentricity squared


def geodetic_to_ecef(lat_deg: float, lon_deg: float, h_m: float) -> Tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)

    N = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
    x = (N + h_m) * cos_lat * cos_lon
    y = (N + h_m) * cos_lat * sin_lon
    z = (N * (1.0 - _WGS84_E2) + h_m) * sin_lat
    return x, y, z


def ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """
    Robust ECEF -> geodetic (WGS84).
    Returns (lat_deg, lon_deg, h_m).
    """
    lon = math.atan2(y, x)
    p = math.sqrt(x * x + y * y)

    # Initial lat approximation
    lat = math.atan2(z, p * (1.0 - _WGS84_E2))
    for _ in range(10):
        sin_lat = math.sin(lat)
        N = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
        h = p / math.cos(lat) - N
        lat_new = math.atan2(z, p * (1.0 - _WGS84_E2 * (N / (N + h))))
        if abs(lat_new - lat) < 1e-12:
            lat = lat_new
            break
        lat = lat_new

    sin_lat = math.sin(lat)
    N = _WGS84_A / math.sqrt(1.0 - _WGS84_E2 * sin_lat * sin_lat)
    h = p / math.cos(lat) - N

    return math.degrees(lat), math.degrees(lon), h


def enu_to_ecef(
    e_m: float,
    n_m: float,
    u_m: float,
    origin_lat_deg: float,
    origin_lon_deg: float,
    origin_h_m: float,
) -> Tuple[float, float, float]:
    """
    ENU(m) at origin -> ECEF(m).
    """
    x0, y0, z0 = geodetic_to_ecef(origin_lat_deg, origin_lon_deg, origin_h_m)

    lat0 = math.radians(origin_lat_deg)
    lon0 = math.radians(origin_lon_deg)

    sin_lat0 = math.sin(lat0)
    cos_lat0 = math.cos(lat0)
    sin_lon0 = math.sin(lon0)
    cos_lon0 = math.cos(lon0)

    # ENU->ECEF rotation
    dx = (-sin_lon0) * e_m + (-sin_lat0 * cos_lon0) * n_m + (cos_lat0 * cos_lon0) * u_m
    dy = (cos_lon0) * e_m + (-sin_lat0 * sin_lon0) * n_m + (cos_lat0 * sin_lon0) * u_m
    dz = (0.0) * e_m + (cos_lat0) * n_m + (sin_lat0) * u_m

    return x0 + dx, y0 + dy, z0 + dz


# ---------------------------
# NMEA helpers
# ---------------------------

def nmea_checksum_xor(body: str) -> str:
    """
    NMEA checksum = XOR of all bytes in body (without $ and without *xx).
    Returns 2-hex uppercase.
    """
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


def format_hhmmss_ss(total_seconds: float) -> str:
    """
    total_seconds -> hhmmss.ss (UTC-like). Supports "time from 0".
    0.0 -> 000000.00
    0.1 -> 000000.10
    """
    if total_seconds < 0:
        total_seconds = 0.0
    total_seconds = total_seconds % (24.0 * 3600.0)

    hours = int(total_seconds // 3600)
    rem = total_seconds - hours * 3600
    minutes = int(rem // 60)
    seconds = rem - minutes * 60

    return f"{hours:02d}{minutes:02d}{seconds:05.2f}"


def deg_to_nmea_lat(lat_deg: float) -> Tuple[str, str]:
    hemi = "N" if lat_deg >= 0 else "S"
    lat_abs = abs(lat_deg)
    d = int(lat_abs)
    m = (lat_abs - d) * 60.0
    return f"{d:02d}{m:07.4f}", hemi  # ddmm.mmmm


def deg_to_nmea_lon(lon_deg: float) -> Tuple[str, str]:
    hemi = "E" if lon_deg >= 0 else "W"
    lon_abs = abs(lon_deg)
    d = int(lon_abs)
    m = (lon_abs - d) * 60.0
    return f"{d:03d}{m:07.4f}", hemi  # dddmm.mmmm


def build_gpgga(
    t_sec: float,
    lat_deg: float,
    lon_deg: float,
    alt_m: float,
    *,
    start_time_sec: float = 0.0,
    fix_quality: int = 1,
    num_sats: int = 8,
    hdop: float = 0.9,
    geoid_sep_m: float = 0.0,
) -> str:
    """
    Builds one $GPGGA sentence (with checksum), matching your sample:
      $GPGGA,<time>,<lat>,N,<lon>,E,1,08,0.9,<alt>,M,0.0,M,,*CS
    """
    hhmmss = format_hhmmss_ss(start_time_sec + t_sec)
    lat_str, ns = deg_to_nmea_lat(lat_deg)
    lon_str, ew = deg_to_nmea_lon(lon_deg)

    body = (
        f"GPGGA,{hhmmss},{lat_str},{ns},{lon_str},{ew},"
        f"{fix_quality:d},{num_sats:02d},{hdop:.1f},{alt_m:.1f},M,{geoid_sep_m:.1f},M,,"
    )
    cs = nmea_checksum_xor(body)
    return f"${body}*{cs}"


# ---------------------------
# CSV -> NMEA conversion
# ---------------------------

@dataclass(frozen=True)
class CsvTrajectorySpec:
    """
    Mapping of CSV columns for Surprize trajectory export.
    """
    time_col: str = "t"
    x_col: str = "X"
    y_col: str = "Y"
    z_col: str = "Z"


@dataclass(frozen=True)
class NmeaGgaParams:
    start_time_sec: float = 0.0
    fix_quality: int = 1
    num_sats: int = 8
    hdop: float = 0.9
    geoid_sep_m: float = 0.0


def load_xyz_csv(
    path: Path,
    *,
    spec: CsvTrajectorySpec = CsvTrajectorySpec(),
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Loads t, X, Y, Z from CSV with header.
    Returns numpy arrays of float64.
    """
    t_list: List[float] = []
    x_list: List[float] = []
    y_list: List[float] = []
    z_list: List[float] = []

    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")

        missing = [c for c in (spec.time_col, spec.x_col, spec.y_col, spec.z_col) if c not in reader.fieldnames]
        if missing:
            raise ValueError(f"CSV missing required columns: {missing}. Present: {reader.fieldnames}")

        for row in reader:
            if not row.get(spec.time_col):
                continue
            t_list.append(float(row[spec.time_col]))
            x_list.append(float(row[spec.x_col]))
            y_list.append(float(row[spec.y_col]))
            z_list.append(float(row[spec.z_col]))

    if not t_list:
        raise ValueError("Empty trajectory (no rows parsed)")

    return (
        np.asarray(t_list, dtype=np.float64),
        np.asarray(x_list, dtype=np.float64),
        np.asarray(y_list, dtype=np.float64),
        np.asarray(z_list, dtype=np.float64),
    )


def resample_to_hz(
    t: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    *,
    sample_hz: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Linear interpolation onto uniform grid 0..t_end with step 1/sample_hz.
    """
    if sample_hz <= 0:
        raise ValueError(f"sample_hz must be > 0, got {sample_hz}")
    if len(t) < 2:
        raise ValueError("Trajectory too short")

    order = np.argsort(t)
    t = t[order]
    x = x[order]
    y = y[order]
    z = z[order]

    # Drop duplicate timestamps (keep first)
    uniq = np.ones_like(t, dtype=bool)
    uniq[1:] = t[1:] != t[:-1]
    t = t[uniq]
    x = x[uniq]
    y = y[uniq]
    z = z[uniq]

    dt = 1.0 / sample_hz
    t_end = float(t[-1])

    n = int(math.floor(t_end / dt + 1e-9)) + 1
    t_grid = np.arange(n, dtype=np.float64) * dt

    xg = np.interp(t_grid, t, x)
    yg = np.interp(t_grid, t, y)
    zg = np.interp(t_grid, t, z)
    return t_grid, xg, yg, zg


def prepend_static_segment(
    t: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    *,
    static_sec: float,
    sample_hz: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Prepends N=static_sec*sample_hz copies of the first point.
    Output time always starts from 0, motion is shifted forward by static duration.
    """
    if static_sec < 0:
        raise ValueError(f"static_sec must be >= 0, got {static_sec}")

    static_samples = int(round(static_sec * sample_hz))
    if static_samples <= 0:
        return t, x, y, z

    dt = 1.0 / sample_hz
    t_static = np.arange(static_samples, dtype=np.float64) * dt
    t_motion = t + (static_samples * dt)

    t_out = np.concatenate([t_static, t_motion])
    x_out = np.concatenate([np.full(static_samples, x[0]), x])
    y_out = np.concatenate([np.full(static_samples, y[0]), y])
    z_out = np.concatenate([np.full(static_samples, z[0]), z])
    return t_out, x_out, y_out, z_out


def write_nmea_gga_from_local_xyz_csv(
    input_csv: Path,
    out_nmea_txt: Path,
    *,
    origin_lat_deg: float,
    origin_lon_deg: float,
    origin_h_m: float,
    static_sec: float = 0.0,
    sample_hz: float = 10.0,
    csv_spec: CsvTrajectorySpec = CsvTrajectorySpec(),
    nmea_params: NmeaGgaParams = NmeaGgaParams(),
) -> None:
    """
    Converts Surprize local trajectory CSV -> NMEA GGA lines for gps-sdr-sim -g.

    Fixed axis contract (agreed):
      local (X,Y,Z) -> ENU (E,N,U) as:
        E = X
        N = Y
        U = Z
    """
    if abs(sample_hz - 10.0) > 1e-9:
        raise ValueError(f"gps-sdr-sim expects 10 Hz; got sample_hz={sample_hz}")
    if not (-90.0 <= origin_lat_deg <= 90.0):
        raise ValueError(f"origin_lat_deg out of range: {origin_lat_deg}")
    if not (-180.0 <= origin_lon_deg <= 180.0):
        raise ValueError(f"origin_lon_deg out of range: {origin_lon_deg}")

    t, x, y, z = load_xyz_csv(input_csv, spec=csv_spec)
    t10, x10, y10, z10 = resample_to_hz(t, x, y, z, sample_hz=sample_hz)
    t_out, x_out, y_out, z_out = prepend_static_segment(t10, x10, y10, z10, static_sec=static_sec, sample_hz=sample_hz)

    out_nmea_txt.parent.mkdir(parents=True, exist_ok=True)

    with out_nmea_txt.open("w", encoding="utf-8", newline="\n") as f:
        for ti, Xi, Yi, Zi in zip(t_out, x_out, y_out, z_out):
            # Fixed mapping local -> ENU
            E = float(Xi)
            N = float(Yi)
            U = float(Zi)

            Xecef, Yecef, Zecef = enu_to_ecef(E, N, U, origin_lat_deg, origin_lon_deg, origin_h_m)
            lat, lon, h = ecef_to_geodetic(Xecef, Yecef, Zecef)

            if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0) or math.isnan(h):
                raise ValueError(f"Invalid geodetic output lat={lat} lon={lon} h={h}")

            line = build_gpgga(
                t_sec=float(ti),
                lat_deg=lat,
                lon_deg=lon,
                alt_m=h,
                start_time_sec=nmea_params.start_time_sec,
                fix_quality=nmea_params.fix_quality,
                num_sats=nmea_params.num_sats,
                hdop=nmea_params.hdop,
                geoid_sep_m=nmea_params.geoid_sep_m,
            )
            f.write(line + "\n")
