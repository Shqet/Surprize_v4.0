from __future__ import annotations

from app.ui.trajectory.csv_loader import TrajectoryCsvLoadTask


def test_csv_loader_emits_duration_from_t_column(tmp_path):
    csv_path = tmp_path / "trajectory.csv"
    csv_path.write_text(
        "t,x,y,z\n"
        "0.0,0,0,0\n"
        "1.5,1,2,3\n"
        "2.0,2,3,4\n",
        encoding="utf-8",
    )

    got = []
    task = TrajectoryCsvLoadTask(seq=7, csv_path=csv_path)
    task.signals.ok.connect(lambda seq, payload: got.append((seq, payload)))
    task.run()

    assert len(got) == 1
    seq, payload = got[0]
    assert seq == 7
    assert payload["points"] == [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (2.0, 3.0, 4.0)]
    assert payload["duration_sec"] == 2.0


def test_csv_loader_emits_none_duration_without_time_column(tmp_path):
    csv_path = tmp_path / "trajectory.csv"
    csv_path.write_text(
        "x,y,z\n"
        "0,0,0\n"
        "1,2,3\n",
        encoding="utf-8",
    )

    got = []
    task = TrajectoryCsvLoadTask(seq=3, csv_path=csv_path)
    task.signals.ok.connect(lambda seq, payload: got.append((seq, payload)))
    task.run()

    assert len(got) == 1
    seq, payload = got[0]
    assert seq == 3
    assert payload["points"] == [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)]
    assert payload["duration_sec"] is None
