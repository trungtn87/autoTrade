"""Whitelisted lab CLI actions for AutoTrade V3 Research Lab Dashboard.

Every action invokes an explicit lab CLI command via ``subprocess.run(..., shell=False)``,
validates all arguments against command injection, refuses baseline edits and
UNCONFIRMED cost profiles on real data, and logs every executed command.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from autotrade_v3.dashboard.queries import DEFAULT_ACTION_LOG, DEFAULT_DATASET_VERIFY_LOG
from autotrade_v3.sim.costs import load_cost_profiles
from autotrade_v3.strategy_core.registry import parse_strategy_ref

ALLOWED_ACTIONS = frozenset(
    {
        "dataset_verify",
        "strategy_clone",
        "edit_clone_params",
        "backtest",
        "uncertainty",
        "verify",
        "diff",
        "export_strategy_core",
    }
)

_FORBIDDEN_CHARS_RE = re.compile(r"[;&|`$><\n\r\x00]")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_@.\-,:/\\]+$")


class ActionNotAllowedError(ValueError):
    """Raised when an action outside the dashboard whitelist is requested."""


class InvalidActionInputError(ValueError):
    """Raised when action arguments contain unsafe characters or violate lab rules."""


@dataclass
class ActionResult:
    action: str
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


def _validate_safe_arg(value: str, *, field_name: str, allow_spaces: bool = False) -> str:
    s = str(value).strip()
    if not s:
        raise InvalidActionInputError(f"{field_name} must not be empty.")
    if _FORBIDDEN_CHARS_RE.search(s):
        raise InvalidActionInputError(f"{field_name} contains forbidden shell characters: {s!r}")
    if ".." in Path(s).parts:
        raise InvalidActionInputError(f"{field_name} must not contain '..' path traversal: {s!r}")
    if not allow_spaces and any(ch.isspace() for ch in s):
        raise InvalidActionInputError(f"{field_name} must not contain whitespace: {s!r}")
    if not allow_spaces and not _SAFE_ID_RE.match(s):
        raise InvalidActionInputError(f"{field_name} contains disallowed characters: {s!r}")
    return s


def _append_action_log(
    *,
    action: str,
    argv: list[str],
    returncode: int,
    duration_s: float,
    log_path: str | Path = DEFAULT_ACTION_LOG,
) -> None:
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "argv": argv,
        "returncode": int(returncode),
        "duration_s": round(float(duration_s), 4),
    }
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec) + "\n")


def _run_whitelisted(
    *,
    action: str,
    argv: list[str],
    log_path: str | Path = DEFAULT_ACTION_LOG,
    timeout_s: int = 1800,
) -> ActionResult:
    if action not in ALLOWED_ACTIONS:
        raise ActionNotAllowedError(f"Action '{action}' is not in whitelist: {sorted(ALLOWED_ACTIONS)}")
    t0 = time.monotonic()
    proc = subprocess.run(
        argv,
        shell=False,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    dur = time.monotonic() - t0
    _append_action_log(
        action=action,
        argv=argv,
        returncode=proc.returncode,
        duration_s=dur,
        log_path=log_path,
    )
    return ActionResult(
        action=action,
        argv=argv,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_s=dur,
    )


def dispatch_action(action: str, params: dict[str, Any] | None = None, *, log_path: str | Path = DEFAULT_ACTION_LOG) -> ActionResult:
    """Single whitelisted dispatcher; rejects any unknown action name."""
    if action not in ALLOWED_ACTIONS:
        raise ActionNotAllowedError(f"Action '{action}' is not allowed.")
    p = dict(params or {})
    if action == "dataset_verify":
        return run_dataset_verify(
            catalog_path=p.get("catalog_path", "catalog/research.duckdb"),
            verify_log_path=p.get("verify_log_path", DEFAULT_DATASET_VERIFY_LOG),
            log_path=log_path,
        )
    if action == "strategy_clone":
        return run_strategy_clone(
            from_ref=p["from_ref"],
            to_ref=p["to_ref"],
            params_dir=p.get("params_dir"),
            log_path=log_path,
        )
    if action == "edit_clone_params":
        return run_edit_clone_params(
            strategy_ref=p["strategy_ref"],
            combo_key=p["combo_key"],
            updates=p["updates"],
            params_dir=p.get("params_dir"),
            log_path=log_path,
        )
    if action == "backtest":
        return run_backtest(
            strategy=p.get("strategy", "20FINAL@1"),
            mode=p.get("mode", "ALL_SIGNALS"),
            symbols=p.get("symbols", "BTC-USDT,ETH-USDT"),
            start=p.get("start", "2023-11-01"),
            end=p.get("end", "2026-11-01"),
            cost_profile=p.get("cost_profile", "IDEAL"),
            ambiguity_policy=p.get("ambiguity_policy", "CONSERVATIVE"),
            catalog_path=p.get("catalog_path", "catalog/research.duckdb"),
            out_dir=p.get("out_dir", "runs/lab"),
            annotation_source=p.get("annotation_source"),
            smoke_test=bool(p.get("smoke_test", False)),
            log_path=log_path,
        )
    if action == "uncertainty":
        return run_uncertainty(
            strategy=p.get("strategy", "20FINAL@1"),
            mode=p.get("mode", "ALL_SIGNALS"),
            symbols=p.get("symbols", "BTC-USDT,ETH-USDT"),
            start=p.get("start", "2023-11-01"),
            end=p.get("end", "2026-11-01"),
            cost_profile=p.get("cost_profile", "IDEAL"),
            catalog_path=p.get("catalog_path", "catalog/research.duckdb"),
            out_json=p.get("out_json"),
            smoke_test=bool(p.get("smoke_test", False)),
            log_path=log_path,
        )
    if action == "verify":
        return run_verify(
            run_dir=p["run_dir"],
            dataset_root=p.get("dataset_root", "."),
            no_write=bool(p.get("no_write", False)),
            output_dir=p.get("output_dir"),
            log_path=log_path,
        )
    if action == "diff":
        return run_diff(
            run_a=p["run_a"],
            run_b=p["run_b"],
            store_dir=p.get("store_dir", "runs/lab/store"),
            out_dir=p.get("out_dir"),
            log_path=log_path,
        )
    if action == "export_strategy_core":
        return run_export_strategy_core(
            strategy_ref=p["strategy_ref"],
            out_path=p["out_path"],
            params_dir=p.get("params_dir"),
            log_path=log_path,
        )
    raise ActionNotAllowedError(f"Action '{action}' is not allowed.")


def run_dataset_verify(
    *,
    catalog_path: str | Path = "catalog/research.duckdb",
    verify_log_path: str | Path = DEFAULT_DATASET_VERIFY_LOG,
    log_path: str | Path = DEFAULT_ACTION_LOG,
) -> ActionResult:
    cat = _validate_safe_arg(str(catalog_path), field_name="catalog_path", allow_spaces=True)
    argv = [sys.executable, "-m", "autotrade_v3.cli", "datasets", "--catalog", cat, "--verify"]
    res = _run_whitelisted(action="dataset_verify", argv=argv, log_path=log_path)
    passed = res.returncode == 0 and "Audit Passed" in res.stdout
    v_p = Path(verify_log_path)
    v_p.parent.mkdir(parents=True, exist_ok=True)
    v_p.write_text(
        json.dumps(
            {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "passed": passed,
                "A1": "PASS" if passed else "FAIL",
                "A2": "PASS" if passed else "FAIL",
                "stdout": res.stdout,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return res


def run_strategy_clone(
    *,
    from_ref: str,
    to_ref: str,
    params_dir: str | Path | None = None,
    log_path: str | Path = DEFAULT_ACTION_LOG,
) -> ActionResult:
    f_ref = _validate_safe_arg(from_ref, field_name="from_ref")
    t_ref = _validate_safe_arg(to_ref, field_name="to_ref")
    argv = [sys.executable, "-m", "autotrade_v3.cli", "strategy", "clone", "--from", f_ref, "--to", t_ref]
    if params_dir is not None:
        argv.extend(["--params-dir", _validate_safe_arg(str(params_dir), field_name="params_dir", allow_spaces=True)])
    return _run_whitelisted(action="strategy_clone", argv=argv, log_path=log_path)


def run_edit_clone_params(
    *,
    strategy_ref: str,
    combo_key: str,
    updates: dict[str, Any],
    params_dir: str | Path | None = None,
    log_path: str | Path = DEFAULT_ACTION_LOG,
) -> ActionResult:
    s_ref = _validate_safe_arg(strategy_ref, field_name="strategy_ref")
    sid, ver = parse_strategy_ref(s_ref)
    if (sid, ver) == ("20FINAL", "1"):
        raise InvalidActionInputError("Baseline 20FINAL@1 is locked and cannot be edited.")
    ck = _validate_safe_arg(combo_key, field_name="combo_key")
    clean_updates: dict[str, float | int | None] = {}
    allowed_fields = {"tp_pct", "sl_pct", "trailing_act_pct", "trailing_cb_pct", "max_bars_held"}
    for k, v in updates.items():
        if k not in allowed_fields:
            raise InvalidActionInputError(f"Unsupported parameter field: {k}")
        if k == "max_bars_held":
            clean_updates[k] = int(v) if v is not None else None
        else:
            clean_updates[k] = float(v)
    argv = [
        sys.executable,
        "-m",
        "autotrade_v3.dashboard._cli_ops",
        "edit-params",
        "--strategy",
        s_ref,
        "--combo",
        ck,
        "--updates-json",
        json.dumps(clean_updates, sort_keys=True),
    ]
    if params_dir is not None:
        argv.extend(["--params-dir", _validate_safe_arg(str(params_dir), field_name="params_dir", allow_spaces=True)])
    return _run_whitelisted(action="edit_clone_params", argv=argv, log_path=log_path)


def run_backtest(
    *,
    strategy: str,
    mode: str,
    symbols: str,
    start: str,
    end: str,
    cost_profile: str,
    ambiguity_policy: str = "CONSERVATIVE",
    catalog_path: str | Path = "catalog/research.duckdb",
    out_dir: str | Path = "runs/lab",
    annotation_source: str | None = None,
    smoke_test: bool = False,
    log_path: str | Path = DEFAULT_ACTION_LOG,
) -> ActionResult:
    strat = _validate_safe_arg(strategy, field_name="strategy")
    md = _validate_safe_arg(mode, field_name="mode")
    if md not in {"ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO", "ALL"}:
        raise InvalidActionInputError(f"Unsupported mode: {md}")
    syms = _validate_safe_arg(symbols, field_name="symbols")
    st = _validate_safe_arg(start, field_name="start")
    en = _validate_safe_arg(end, field_name="end")
    cp = _validate_safe_arg(cost_profile, field_name="cost_profile")
    ap = _validate_safe_arg(ambiguity_policy, field_name="ambiguity_policy")
    if ap not in {"CONSERVATIVE", "OPTIMISTIC", "OHLC_PATH", "LEGACY"}:
        raise InvalidActionInputError(f"Unsupported ambiguity_policy: {ap}")

    profiles = load_cost_profiles()
    if cp not in profiles:
        raise InvalidActionInputError(f"Unknown cost_profile: {cp}")
    if not smoke_test and profiles[cp].status != "CONFIRMED":
        raise InvalidActionInputError(
            f"Cost profile '{cp}' has status '{profiles[cp].status}' and cannot be selected for real data."
        )

    cat = _validate_safe_arg(str(catalog_path), field_name="catalog_path", allow_spaces=True)
    od = _validate_safe_arg(str(out_dir), field_name="out_dir", allow_spaces=True)

    argv = [
        sys.executable,
        "-m",
        "autotrade_v3.cli",
        "backtest",
        "--strategy",
        strat,
        "--mode",
        md,
        "--symbols",
        syms,
        "--start",
        st,
        "--end",
        en,
        "--cost-profile",
        cp,
        "--ambiguity-policy",
        ap,
        "--catalog",
        cat,
        "--out-dir",
        od,
    ]
    if annotation_source:
        argv.extend(["--annotation-source", _validate_safe_arg(annotation_source, field_name="annotation_source")])
    if smoke_test:
        argv.append("--smoke-test")
    return _run_whitelisted(action="backtest", argv=argv, log_path=log_path)


def run_uncertainty(
    *,
    strategy: str,
    mode: str,
    symbols: str,
    start: str,
    end: str,
    cost_profile: str,
    catalog_path: str | Path = "catalog/research.duckdb",
    out_json: str | Path | None = None,
    smoke_test: bool = False,
    log_path: str | Path = DEFAULT_ACTION_LOG,
) -> ActionResult:
    strat = _validate_safe_arg(strategy, field_name="strategy")
    md = _validate_safe_arg(mode, field_name="mode")
    syms = _validate_safe_arg(symbols, field_name="symbols")
    st = _validate_safe_arg(start, field_name="start")
    en = _validate_safe_arg(end, field_name="end")
    cp = _validate_safe_arg(cost_profile, field_name="cost_profile")
    profiles = load_cost_profiles()
    if cp not in profiles or (not smoke_test and profiles[cp].status != "CONFIRMED"):
        raise InvalidActionInputError(f"Cost profile '{cp}' cannot be used on real data.")
    cat = _validate_safe_arg(str(catalog_path), field_name="catalog_path", allow_spaces=True)
    argv = [
        sys.executable,
        "-m",
        "autotrade_v3.cli",
        "uncertainty",
        "--strategy",
        strat,
        "--mode",
        md,
        "--symbols",
        syms,
        "--start",
        st,
        "--end",
        en,
        "--cost-profile",
        cp,
        "--catalog",
        cat,
    ]
    if out_json is not None:
        argv.extend(["--out-json", _validate_safe_arg(str(out_json), field_name="out_json", allow_spaces=True)])
    if smoke_test:
        argv.append("--smoke-test")
    return _run_whitelisted(action="uncertainty", argv=argv, log_path=log_path)


def run_verify(
    *,
    run_dir: str | Path,
    dataset_root: str | Path = ".",
    no_write: bool = False,
    output_dir: str | Path | None = None,
    log_path: str | Path = DEFAULT_ACTION_LOG,
) -> ActionResult:
    rd = _validate_safe_arg(str(run_dir), field_name="run_dir", allow_spaces=True)
    dr = _validate_safe_arg(str(dataset_root), field_name="dataset_root", allow_spaces=True)
    argv = [sys.executable, "-m", "autotrade_v3.cli", "verify", rd, "--dataset-root", dr]
    if no_write:
        argv.append("--no-write")
    if output_dir is not None:
        argv.extend(["--output-dir", _validate_safe_arg(str(output_dir), field_name="output_dir", allow_spaces=True)])
    return _run_whitelisted(action="verify", argv=argv, log_path=log_path)


def run_diff(
    *,
    run_a: str,
    run_b: str,
    store_dir: str | Path = "runs/lab/store",
    out_dir: str | Path | None = None,
    log_path: str | Path = DEFAULT_ACTION_LOG,
) -> ActionResult:
    ra = _validate_safe_arg(run_a, field_name="run_a")
    rb = _validate_safe_arg(run_b, field_name="run_b")
    sd = _validate_safe_arg(str(store_dir), field_name="store_dir", allow_spaces=True)
    argv = [sys.executable, "-m", "autotrade_v3.cli", "diff", ra, rb, "--store-dir", sd]
    if out_dir is not None:
        argv.extend(["--out", _validate_safe_arg(str(out_dir), field_name="out_dir", allow_spaces=True)])
    return _run_whitelisted(action="diff", argv=argv, log_path=log_path)


def run_export_strategy_core(
    *,
    strategy_ref: str,
    out_path: str | Path,
    params_dir: str | Path | None = None,
    log_path: str | Path = DEFAULT_ACTION_LOG,
) -> ActionResult:
    s_ref = _validate_safe_arg(strategy_ref, field_name="strategy_ref")
    op = _validate_safe_arg(str(out_path), field_name="out_path", allow_spaces=True)
    argv = [
        sys.executable,
        "-m",
        "autotrade_v3.dashboard._cli_ops",
        "export-strategy",
        "--strategy",
        s_ref,
        "--out",
        op,
    ]
    if params_dir is not None:
        argv.extend(["--params-dir", _validate_safe_arg(str(params_dir), field_name="params_dir", allow_spaces=True)])
    return _run_whitelisted(action="export_strategy_core", argv=argv, log_path=log_path)
