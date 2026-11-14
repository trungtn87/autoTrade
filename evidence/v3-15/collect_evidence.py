"""Collect V3-15 evidence artifacts directly from real RunStore and real M1 catalog."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import random
import socket
import subprocess
import sys
import time
import urllib.request

import duckdb
import pandas as pd
from streamlit.testing.v1 import AppTest

from autotrade_v3.dashboard import queries
from autotrade_v3.tools.recompute_metrics import recompute_metrics

EVIDENCE_DIR = Path("evidence/v3-15")
DATA_TESTS_DIR = EVIDENCE_DIR / "data_tests"
SCREENS_DIR = EVIDENCE_DIR / "screens"
STORE_DIR = Path("runs/lab/store")
CATALOG_PATH = Path("catalog/research.duckdb")
PAGES_DIR = Path("autotrade_v3/dashboard/pages")


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_hashes() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(STORE_DIR.rglob("*")):
        if p.is_file() and p.name != "index.duckdb":
            out[str(p)] = _sha256_file(p)
    for bp in (
        Path("autotrade_v3/strategy_core/strategies/final20/params/20final_v1.json"),
        Path("autotrade_v3/strategy_core/strategies/final20/params/20final_v1.lock"),
    ):
        if bp.exists():
            out[str(bp)] = _sha256_file(bp)
    return out


def write_data_tests(runs: list[dict]) -> None:
    DATA_TESTS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Datasets
    ds_ov = queries.get_datasets_overview(catalog_path=CATALOG_PATH, store_dir=STORE_DIR)
    prov_df = queries.get_dataset_provider_breakdown(catalog_path=CATALOG_PATH)
    ds_lines = [
        "=== SCREEN 1: DATASETS DATA TEST ===",
        json.dumps(ds_ov, indent=2),
        "--- Provider Breakdown ---",
        prov_df.to_string(index=False),
    ]
    (DATA_TESTS_DIR / "datasets.log").write_text("\n".join(ds_lines) + "\n", encoding="utf-8")

    # 2. Strategies
    strats = queries.list_strategies_info()
    st_lines = ["=== SCREEN 2: STRATEGIES DATA TEST ===", json.dumps(strats, indent=2)]
    (DATA_TESTS_DIR / "strategies.log").write_text("\n".join(st_lines) + "\n", encoding="utf-8")

    # 3. New Run
    nr_opts = queries.get_new_run_options(catalog_path=CATALOG_PATH)
    (DATA_TESTS_DIR / "new_run.log").write_text(
        "=== SCREEN 3: NEW RUN OPTIONS DATA TEST ===\n" + json.dumps(nr_opts, indent=2) + "\n",
        encoding="utf-8",
    )

    # 4. Run Detail (queries vs independent recompute_metrics)
    rd_lines = ["=== SCREEN 4: RUN DETAIL DATA TEST (queries.py vs recompute_metrics.py) ==="]
    for r in runs:
        rid = str(r["run_id"])
        rdir = STORE_DIR / rid
        q_sum = queries.get_run_summary(rid, store_dir=STORE_DIR)
        indep = recompute_metrics(rdir / "legs.parquet")
        diff_net = abs(q_sum["net_pnl"] - indep["total_net_pnl"])
        diff_gross = abs(q_sum["gross_pnl"] - indep["total_gross_pnl"])
        diff_fees = abs(q_sum["total_fees"] - indep["total_fees"])
        rd_lines.append(
            f"run_id={rid} mode={q_sum['execution_mode']} status={q_sum['validation_status']} | "
            f"queries_net={q_sum['net_pnl']:.6f} indep_net={indep['total_net_pnl']:.6f} diff_net={diff_net:.3e} | "
            f"diff_gross={diff_gross:.3e} diff_fees={diff_fees:.3e} | "
            f"entries={q_sum['accepted_entries']}=={indep['total_entries']} legs={q_sum['total_legs']}=={indep['total_legs']}"
        )
    (DATA_TESTS_DIR / "run_detail.log").write_text("\n".join(rd_lines) + "\n", encoding="utf-8")

    # 5. Signal Explorer (30 seeded signals)
    all_sig_run = next(r for r in runs if r["execution_mode"] == "ALL_SIGNALS")
    rid = str(all_sig_run["run_id"])
    rdir = STORE_DIR / rid
    legs_df = pd.read_parquet(rdir / "legs.parquet")
    rng = random.Random(20261114)
    ambig_mask = legs_df["intrabar_ambiguous"].fillna(False).astype(bool) | legs_df["intrabar_trailing_ambiguous"].fillna(False).astype(bool)
    pools = [
        ("AMBIGUOUS", sorted(set(legs_df[ambig_mask]["signal_id"].astype(str))), 6),
        ("TP", sorted(set(legs_df[legs_df["exit_reason"] == "TP"]["signal_id"].astype(str))), 8),
        ("SL", sorted(set(legs_df[legs_df["exit_reason"] == "SL"]["signal_id"].astype(str))), 8),
        ("TRAIL", sorted(set(legs_df[legs_df["exit_reason"] == "TRAIL"]["signal_id"].astype(str))), 8),
    ]
    picked: list[tuple[str, str]] = []
    seen: set[str] = set()
    for tag, pool, k in pools:
        avail = [s for s in pool if s not in seen]
        for s in rng.sample(avail, min(k, len(avail))):
            picked.append((tag, s))
            seen.add(s)

    se_lines = [f"=== SCREEN 5: SIGNAL EXPLORER 30 SIGNALS (run_id={rid}, seed=20261114) ==="]
    csv_rows: list[dict] = []
    con = duckdb.connect(str(CATALOG_PATH), read_only=True)
    try:
        for tag, sid in picked:
            det = queries.get_signal_detail_and_chart(rid, sid, store_dir=STORE_DIR, catalog_path=CATALOG_PATH)
            s_legs = legs_df[legs_df["signal_id"].astype(str) == sid].reset_index(drop=True)
            lv = det["levels"]
            leg_entry = float(s_legs["entry_px_raw"].iloc[0])
            side = str(s_legs["side"].iloc[0]).upper()
            tp_pct = float(s_legs["tp_pct"].iloc[0])
            sl_pct = float(s_legs["sl_pct"].iloc[0])
            exp_tp = leg_entry * (1.0 + tp_pct) if side == "LONG" else leg_entry * (1.0 - tp_pct)
            exp_sl = leg_entry * (1.0 - sl_pct) if side == "LONG" else leg_entry * (1.0 + sl_pct)
            m1_df = det["m1_path_df"]
            cat_m1 = con.execute(
                "SELECT close FROM candles WHERE symbol = ? AND timestamp_ms >= ? AND timestamp_ms <= ? ORDER BY timestamp_ms ASC",
                [str(s_legs["symbol"].iloc[0]), int(m1_df["time_ms"].iloc[0]), int(m1_df["time_ms"].iloc[-1])],
            ).df()
            m1_match = len(m1_df) == len(cat_m1) and bool((m1_df["close"].to_numpy() == cat_m1["close"].to_numpy()).all())
            entry_diff = abs(lv["entry_price"] - leg_entry)
            tp_diff = abs((lv["tp_price"] or 0.0) - exp_tp)
            sl_diff = abs((lv["sl_price"] or 0.0) - exp_sl)
            matched = entry_diff < 1e-9 and tp_diff < 1e-9 and sl_diff < 1e-9 and m1_match
            se_lines.append(
                f"signal_id={sid} group={tag} symbol={s_legs['symbol'].iloc[0]} side={side} "
                f"entry_diff={entry_diff:.2e} tp_diff={tp_diff:.2e} sl_diff={sl_diff:.2e} m1_match={m1_match} match={matched}"
            )
            csv_rows.append(
                {
                    "signal_id": sid,
                    "group": tag,
                    "symbol": str(s_legs["symbol"].iloc[0]),
                    "side": side,
                    "q_entry": lv["entry_price"],
                    "ledger_entry": leg_entry,
                    "q_tp": lv["tp_price"],
                    "ledger_tp": exp_tp,
                    "q_sl": lv["sl_price"],
                    "ledger_sl": exp_sl,
                    "q_trail_act": lv["trailing_act_price"],
                    "m1_bars": len(m1_df),
                    "m1_match": m1_match,
                    "all_match": matched,
                }
            )
    finally:
        con.close()

    (DATA_TESTS_DIR / "signal_explorer.log").write_text("\n".join(se_lines) + "\n", encoding="utf-8")
    with (EVIDENCE_DIR / "signal_explorer_30.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
        writer.writeheader()
        writer.writerows(csv_rows)

    # 6. Compare
    rids = [str(r["run_id"]) for r in runs[:2]]
    diff_res = queries.get_compare_signal_diff(rids[0], rids[1], store_dir=STORE_DIR)
    (DATA_TESTS_DIR / "compare.log").write_text(
        f"=== SCREEN 6: COMPARE DATA TEST ({rids[0]} vs {rids[1]}) ===\n"
        + json.dumps(diff_res["summary"], indent=2)
        + "\n",
        encoding="utf-8",
    )

    # 7. Research Log
    rlog = queries.list_research_log(store_dir=STORE_DIR)
    (DATA_TESTS_DIR / "research_log.log").write_text(
        "=== SCREEN 7: RESEARCH LOG DATA TEST ===\n" + json.dumps(rlog, indent=2) + "\n",
        encoding="utf-8",
    )


def run_offline_check() -> None:
    lines = ["=== B9 / AC6 OFFLINE SOCKET-BLOCKING VERIFICATION ==="]
    attempted: list[str] = []
    orig_connect = socket.socket.connect
    orig_create = socket.create_connection
    loopback = {"127.0.0.1", "localhost", "::1", ""}

    def _guard_connect(self, address):
        host = address[0] if isinstance(address, tuple) and address else str(address)
        attempted.append(f"socket.connect({address})")
        if str(host) not in loopback:
            raise PermissionError(f"Blocked non-loopback: {address}")
        return orig_connect(self, address)

    def _guard_create(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) and address else str(address)
        attempted.append(f"socket.create_connection({address})")
        if str(host) not in loopback:
            raise PermissionError(f"Blocked non-loopback: {address}")
        return orig_create(address, *args, **kwargs)

    socket.socket.connect = _guard_connect  # type: ignore[method-assign]
    socket.create_connection = _guard_create  # type: ignore[assignment]
    try:
        for pf in sorted(PAGES_DIR.glob("p*.py")):
            at = AppTest.from_file(str(pf), default_timeout=30)
            at.run()
            lines.append(f"page={pf.name} exceptions={len(at.exception)}")
            if at.exception:
                raise RuntimeError(f"Offline AppTest failed on {pf.name}: {at.exception}")
    finally:
        socket.socket.connect = orig_connect  # type: ignore[method-assign]
        socket.create_connection = orig_create  # type: ignore[assignment]

    # Also start real Streamlit server on 127.0.0.1:8599 and fetch HTTP 200
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "autotrade_v3/dashboard/app.py",
            "--server.address",
            "127.0.0.1",
            "--server.port",
            "8599",
            "--server.headless",
            "true",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        status_code = 0
        for _ in range(30):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8599", timeout=2) as resp:
                    status_code = resp.status
                    break
            except Exception:
                time.sleep(0.5)
        lines.append(f"streamlit_server_127.0.0.1:8599 http_status={status_code}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    lines.append(f"socket_attempts_count={len(attempted)}")
    for a in attempted:
        lines.append(f"  {a}")
    (EVIDENCE_DIR / "offline.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    before = snapshot_hashes()
    runs = queries.list_store_runs(STORE_DIR)
    if not runs:
        raise RuntimeError("RunStore is empty; populate runs/lab/store first.")
    write_data_tests(runs)
    run_offline_check()
    after = snapshot_hashes()

    changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    hash_lines = [
        "=== STORE & BASELINE HASHES BEFORE/AFTER ===",
        f"tracked_files={len(before)} changed_files={len(changed)}",
    ]
    for k in sorted(before):
        hash_lines.append(f"{before[k]}  {k}")
    if changed:
        hash_lines.append("CHANGED: " + ", ".join(changed))
    (EVIDENCE_DIR / "store_hash_before_after.txt").write_text("\n".join(hash_lines) + "\n", encoding="utf-8")
    print(f"Evidence collected successfully. tracked={len(before)} changed={len(changed)}")


if __name__ == "__main__":
    main()
