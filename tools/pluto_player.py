# tools/test_pluto_player.py
from __future__ import annotations

import argparse
import shlex
import subprocess
import time
from pathlib import Path

from app.services.gps_sdr_sim.engine import build_run_paths, ensure_dirs, save_cmdline


def _find_repo_root(start: Path) -> Path:
    """
    Repo root is expected to contain:
      - app/
      - bin/
      - tools/
    We start from this file location and walk upwards.
    """
    cur = start.resolve()
    for _ in range(8):
        if (cur / "app").is_dir() and (cur / "bin").is_dir() and (cur / "tools").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise SystemExit(f"FAIL: cannot find repo root from {start} (expected folders: app/, bin/, tools/)")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Step3 CLI test: run PlutoPlayer as long process (black-box), log and terminate.\n"
            "Uses repo-local bin/pluto/PlutoPlayer.exe and outputs/gps_sdr_sim/<run_id>/sim/gpssim_iq.bin.\n"
            "CLI per docs: PlutoPlayer.exe -t <file> [-a <att_db>] [-b <bw_mhz>]\n"
            "Behavior: if --hold-sec is omitted, waits until PlutoPlayer exits. Ctrl+C terminates transmission."
        )
    )

    p.add_argument("--out-root", default="outputs", help="outputs root (default: outputs)")
    p.add_argument("--run-id", required=True, help="existing run_id that already has sim/gpssim_iq.bin")

    # Per manual:
    #   plutoplayer -t gpssim.bin
    #   plutoplayer -t gpssim.bin -a -30.0  (default -20.0, range 0..-80 step 0.25)
    #   plutoplayer -t gpssim.bin -b 3.0    (default 3.0, range 1..5)
    p.add_argument(
        "--tx-atten-db",
        type=float,
        default=-20.0,
        help="TX attenuation in dB. Default -20.0. Range: 0.0 .. -80.0 (step 0.25).",
    )
    p.add_argument(
        "--rf-bw-mhz",
        type=float,
        default=3.0,
        help="RF bandwidth in MHz. Default 3.0. Range: 1.0 .. 5.0.",
    )

    # Optional: if omitted, wait until process exits
    p.add_argument(
        "--hold-sec",
        type=float,
        default=None,
        help="Keep process alive for N seconds, then terminate. If omitted, wait until process exits.",
    )
    p.add_argument("--grace-sec", type=float, default=5.0, help="terminate grace period before kill()")
    p.add_argument("--min-alive-sec", type=float, default=1.0, help="fail if process exits earlier than this (0 disables)")

    # extra flags if needed
    p.add_argument(
        "--extra-args",
        default="",
        help='extra args appended (shell-like). Example: --extra-args "--foo 1 --bar \\"x y\\""',
    )

    return p.parse_args()


def _tail(text: str, n: int = 40) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-n:]) if lines else ""


def _validate_tx_atten_db(v: float) -> None:
    # Manual: applicable range 0.0dB to -80.0dB in 0.25dB steps.
    if v > 0.0 or v < -80.0:
        raise SystemExit(f"FAIL: --tx-atten-db out of range: {v} (expected 0.0 .. -80.0)")
    step = 0.25
    k = round(v / step)
    if abs(v - (k * step)) > 1e-6:
        raise SystemExit(f"FAIL: --tx-atten-db must be in 0.25 dB steps: got {v}")


def _validate_rf_bw_mhz(v: float) -> None:
    # Manual: applicable range 1.0MHz to 5.0MHz
    if v < 1.0 or v > 5.0:
        raise SystemExit(f"FAIL: --rf-bw-mhz out of range: {v} (expected 1.0 .. 5.0)")


def _terminate_process(proc: subprocess.Popen, grace: float) -> None:
    print("TERM: terminate()")
    try:
        proc.terminate()
    except Exception as e:
        print(f"WARN: terminate failed: {e}")

    try:
        proc.wait(timeout=grace)
        print(f"EXIT: rc={proc.returncode}")
    except subprocess.TimeoutExpired:
        print("KILL: grace timeout, kill()")
        try:
            proc.kill()
        except Exception as e:
            print(f"WARN: kill failed: {e}")
        proc.wait(timeout=5.0)
        print(f"EXIT: rc={proc.returncode}")


def _resolve_out_root(repo_root: Path, out_root_arg: str) -> Path:
    p = Path(out_root_arg)
    if p.is_absolute():
        return p.resolve()
    return (repo_root / p).resolve()


def main() -> int:
    args = _parse_args()

    _validate_tx_atten_db(args.tx_atten_db)
    _validate_rf_bw_mhz(args.rf_bw_mhz)

    # Resolve repo root and fixed PlutoPlayer location
    this_file = Path(__file__).resolve()
    repo_root = _find_repo_root(this_file.parent)
    pluto_exe = (repo_root / "bin" / "pluto" / "PlutoPlayer.exe").resolve()
    if not pluto_exe.exists():
        raise SystemExit(f"FAIL: PlutoPlayer.exe not found at expected path: {pluto_exe}")

    out_root = _resolve_out_root(repo_root, args.out_root)
    run_id = args.run_id

    # Your engine already knows outputs structure (outputs/gps_sdr_sim/<run_id>/...)
    paths = build_run_paths(out_root, run_id)
    ensure_dirs(paths)

    iq_src = paths.sim_iq_bin
    if not iq_src.exists():
        raise SystemExit(f"FAIL: IQ file not found (run step2 first): {iq_src}")

    # Copy IQ into run_dir/pluto and use relative path with cwd=pluto_dir
    iq_dst = paths.pluto_dir / iq_src.name
    iq_dst.write_bytes(iq_src.read_bytes())

    cwd = paths.pluto_dir
    iq_arg = iq_dst.name  # relative

    # Per docs: plutoplayer -t gpssim.bin [-a -30.0] [-b 3.0]
    argv = [
        str(pluto_exe),
        "-t",
        iq_arg,
        "-a",
        f"{args.tx_atten_db:.2f}",
        "-b",
        f"{args.rf_bw_mhz:.2f}",
    ]
    if args.extra_args:
        argv += shlex.split(args.extra_args)

    save_cmdline(paths.pluto_cmdline_txt, argv)

    stdout_path = paths.stdout_pluto_log
    stderr_path = paths.stderr_pluto_log

    print(f"RUN: run_id={run_id}")
    print(f"  repo:   {repo_root}")
    print(f"  cwd:    {cwd}")
    print(f"  exe:    {pluto_exe}")
    print(f"  iq:     {iq_dst} (arg: {iq_arg})")
    print(f"  atten:  {args.tx_atten_db} dB")
    print(f"  rfbw:   {args.rf_bw_mhz} MHz")
    print(f"  cmd:    {paths.pluto_cmdline_txt}")
    print(f"  stdout: {stdout_path}")
    print(f"  stderr: {stderr_path}")

    start_ts = time.time()

    with stdout_path.open("w", encoding="utf-8", errors="ignore") as f_out, stderr_path.open(
        "w", encoding="utf-8", errors="ignore"
    ) as f_err:
        p = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=f_out,
            stderr=f_err,
            text=True,
        )

        print(f"PID: {p.pid}")
        if p.pid is None:
            raise SystemExit("FAIL: process did not start (pid is None)")

        # Wait until min_alive_sec (if enabled), then check it didn't exit too early.
        if args.min_alive_sec > 0:
            time.sleep(max(0.0, min(args.min_alive_sec, 2.0)))  # cap just in case
            alive_for = time.time() - start_ts
            if p.poll() is not None and alive_for < args.min_alive_sec:
                rc = p.returncode
                # flush before reading tails (Windows file locks / buffering)
                try:
                    f_out.flush()
                    f_err.flush()
                except Exception:
                    pass
                out_tail = _tail(stdout_path.read_text(encoding="utf-8", errors="ignore"))
                err_tail = _tail(stderr_path.read_text(encoding="utf-8", errors="ignore"))
                print("---- STDERR (tail) ----")
                print(err_tail or "<empty>")
                print("---- STDOUT (tail) ----")
                print(out_tail or "<empty>")
                raise SystemExit(f"FAIL: exited too fast (alive {alive_for:.2f}s) rc={rc}")

        try:
            if args.hold_sec is None:
                print("MODE: wait-until-exit (Ctrl+C to stop transmission)")
                while True:
                    rc = p.poll()
                    if rc is not None:
                        print(f"EXIT: rc={rc}")
                        break
                    time.sleep(0.5)
            else:
                remaining = max(0.0, args.hold_sec - (time.time() - start_ts))
                if remaining > 0:
                    time.sleep(remaining)
                if p.poll() is None:
                    _terminate_process(p, args.grace_sec)
                else:
                    print(f"NOTE: process already exited rc={p.returncode}")

        except KeyboardInterrupt:
            print("INTERRUPT: stopping transmission...")
            if p.poll() is None:
                _terminate_process(p, args.grace_sec)
            else:
                print(f"NOTE: process already exited rc={p.returncode}")

    # Self-checks
    if not stdout_path.exists() or not stderr_path.exists():
        raise SystemExit("FAIL: stdout/stderr logs not written")

    print("SELF-CHECK: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
