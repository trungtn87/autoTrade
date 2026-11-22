"""Kiểm thử nút 'Đồng bộ' trên dashboard một trang (autotrade_v3.dashboard.sync_runner & one_page)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import time

from autotrade_v3.dashboard import sync_runner
from autotrade_v3.reconcile_v3.labels_vi import RECONCILE_LABELS_VI, find_english_label_leaks


def test_sync_missing_env_stops_without_running(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    calls = 0

    def _fake_popen(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("Không được chạy subprocess khi thiếu SUPABASE_DB_URL")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    ev_dir = tmp_path / "ev"
    accepted, msg = sync_runner.trigger_manual_sync(
        repo_root=tmp_path,
        data_root=tmp_path / "bingx",
        evidence_dir=ev_dir,
    )
    assert accepted is False
    assert "SUPABASE_DB_URL" in msg
    assert calls == 0
    st = sync_runner.read_sync_state(ev_dir)
    assert st["status"] == "ERROR"


def test_sync_fake_collector_updates_state_and_cooldown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://ro_user:fake_pw@localhost:5432/postgres")
    ev_dir = tmp_path / "ev"

    class _FakeProc:
        pid = 12345
        returncode = 0

        def communicate(self, timeout=None):
            return ("LÀM MỚI 17a HOÀN TẤT | sha256=deadbeef", "")

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FakeProc())
    accepted, msg = sync_runner.trigger_manual_sync(
        repo_root=tmp_path,
        data_root=tmp_path / "bingx",
        evidence_dir=ev_dir,
        cooldown_seconds=60,
    )
    assert accepted is True
    assert msg == RECONCILE_LABELS_VI["sync_running"]

    # Chờ worker thread ghi xong trạng thái SUCCESS
    t = sync_runner._ACTIVE_THREADS[str(ev_dir)]
    t.join(timeout=5)
    st = sync_runner.read_sync_state(ev_dir)
    assert st["status"] == "SUCCESS"
    assert st["message_vi"].startswith("Xong lúc ")

    # Chặn bấm liên tiếp trong vòng 60 giây
    accepted2, msg2 = sync_runner.trigger_manual_sync(
        repo_root=tmp_path,
        data_root=tmp_path / "bingx",
        evidence_dir=ev_dir,
        cooldown_seconds=60,
    )
    assert accepted2 is False
    assert "60 giây" in msg2


def test_sync_auth_error_stops_and_redacts_secrets(tmp_path: Path, monkeypatch) -> None:
    secret_url = "postgresql://postgres:SuperSecretPass99@db.example.com:5432/postgres"
    monkeypatch.setenv("SUPABASE_DB_URL", secret_url)
    ev_dir = tmp_path / "ev"
    calls = 0

    class _FailProc:
        pid = 9999
        returncode = 1

        def communicate(self, timeout=None):
            return ("", f"LỖI XÁC THỰC COLLECTOR: password authentication failed for {secret_url}")

    def _fake_popen(*a, **kw):
        nonlocal calls
        calls += 1
        return _FailProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    accepted, _ = sync_runner.trigger_manual_sync(
        repo_root=tmp_path,
        data_root=tmp_path / "bingx",
        evidence_dir=ev_dir,
        cooldown_seconds=0,
    )
    assert accepted is True
    sync_runner._ACTIVE_THREADS[str(ev_dir)].join(timeout=5)

    assert calls == 1  # Dừng ngay, không thử lại
    st = sync_runner.read_sync_state(ev_dir)
    assert st["status"] == "ERROR"
    assert "SuperSecretPass99" not in st["message_vi"]
    assert "postgresql://" not in st["message_vi"]
    assert "[ĐÃ_CHE_BÍ_MẬT]" in st["message_vi"]


def test_sync_lock_blocks_concurrent_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://ro_user:fake_pw@localhost:5432/postgres")
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir(parents=True)
    # Giả lập lock đang chạy
    (ev_dir / ".sync.lock").write_text("99999", encoding="utf-8")

    accepted, msg = sync_runner.trigger_manual_sync(
        repo_root=tmp_path,
        data_root=tmp_path / "bingx",
        evidence_dir=ev_dir,
    )
    assert accepted is False
    assert "đồng thời" in msg


def test_freshness_thresholds_6h_and_24h() -> None:
    now = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
    lvl_ok, _, _ = sync_runner.evaluate_sync_freshness("2026-11-22T10:00:00Z", warn_hours=6, critical_hours=24, now_utc=now)
    lvl_amber, _, _ = sync_runner.evaluate_sync_freshness("2026-11-22T04:00:00Z", warn_hours=6, critical_hours=24, now_utc=now)
    lvl_red, _, _ = sync_runner.evaluate_sync_freshness("2026-11-21T04:00:00Z", warn_hours=6, critical_hours=24, now_utc=now)
    assert lvl_ok == "GREEN"
    assert lvl_amber == "AMBER"
    assert lvl_red == "RED"


def test_no_english_label_leaks_and_loopback_serve(monkeypatch) -> None:
    from autotrade_v3.dashboard import __main__ as dmain
    from streamlit.web import cli as stcli

    sample_html = "<div>" + " ".join(
        [
            RECONCILE_LABELS_VI["sync_btn_label"],
            RECONCILE_LABELS_VI["sync_running"],
            RECONCILE_LABELS_VI["sync_last_at"].format(time="13:00:00 22/11/2026"),
        ]
    ) + "</div>"
    assert find_english_label_leaks(sample_html) == []

    captured: list[str] = []

    def _fake_stcli_main() -> int:
        import sys
        captured.extend(sys.argv)
        return 0

    monkeypatch.setattr(stcli, "main", _fake_stcli_main)
    rc = dmain.main(["serve"])
    assert rc == 0
    assert "--server.address=127.0.0.1" in captured
    _ = time.monotonic()
