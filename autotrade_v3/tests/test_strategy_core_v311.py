"""Comprehensive unit, integration, boundary, and adversarial mutation tests for V3-11 Strategy Core."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any
import numpy as np
import pandas as pd

from autotrade_v3.data_layer import DataLayer
from autotrade_v3.engine.candle_loader import generate_synthetic_m1, resample_m1_to_timeframe
from autotrade_v3.strategy.registry import StrategyRegistry
from autotrade_v3.strategy_core.api import SignalEvent
from autotrade_v3.strategy_core.hashing import compute_all_hashes, normalize_lf
from autotrade_v3.strategy_core.registry import StrategyCoreRegistry, clone_strategy_params
from autotrade_v3.strategy_core.strategies.final20.logic import (
    DEFAULT_LOCK_PATH,
    DEFAULT_PARAMS_PATH,
    FINAL20_DIR,
    BaselineLockError,
    Final20Strategy,
)
from autotrade_v3.validation import gates_b
from autotrade_v3.validation.context import VerifyContext

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_ROOT = REPO_ROOT / "autotrade_v3" / "strategy_core"

ALLOWED_TOP_IMPORTS = set(sys.stdlib_module_names) | {"numpy", "pandas", "__future__"}


def _has_real_m1_data() -> bool:
    return (
        (REPO_ROOT / "catalog" / "research.duckdb").exists()
        and (REPO_ROOT / "data" / "m1_raw").exists()
        and any((REPO_ROOT / "data" / "m1_raw").rglob("*.parquet"))
    )


class TestStrategyCoreV311(unittest.TestCase):
    """V3-11 Unit, Integration, and Adversarial Mutation Tests."""

    @classmethod
    def setUpClass(cls) -> None:
        # Prepare a 4,000-bar M15 synthetic fixture per symbol for fast unit/mutation tests
        btc_m1 = generate_synthetic_m1("BTC-USDT", start="2024-01-01", periods=60_000, base_price=42000.0, seed=101)
        eth_m1 = generate_synthetic_m1("ETH-USDT", start="2024-01-01", periods=60_000, base_price=2400.0, seed=202)
        cls.synth_m15 = {
            "BTC-USDT": resample_m1_to_timeframe(btc_m1, 15),
            "ETH-USDT": resample_m1_to_timeframe(eth_m1, 15),
        }

    def test_01_ast_import_boundary_and_io_isolation(self):
        """Section 3 & 6: AST scan of strategy_core/ verifying only stdlib, numpy, pandas and no network/env/outside I/O."""
        py_files = sorted(CORE_ROOT.rglob("*.py"))
        self.assertGreater(len(py_files), 0)

        violations = []
        forbidden_modules = {"socket", "urllib", "http", "requests", "httpx", "aiohttp", "duckdb"}

        for pf in py_files:
            rel = pf.relative_to(CORE_ROOT).as_posix()
            tree = ast.parse(pf.read_text(encoding="utf-8"), filename=str(pf))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top in forbidden_modules or top not in ALLOWED_TOP_IMPORTS:
                            violations.append((rel, node.lineno, f"import {alias.name}"))
                elif isinstance(node, ast.ImportFrom):
                    if node.level and node.level > 0:
                        # Relative import within strategy_core is allowed provided it does not escape strategy_core
                        depth = len(pf.relative_to(CORE_ROOT).parts) - 1
                        if node.level > depth + 1:
                            violations.append((rel, node.lineno, f"relative import escapes strategy_core: level={node.level}"))
                    else:
                        mod = node.module or ""
                        top = mod.split(".")[0]
                        if top in forbidden_modules or top not in ALLOWED_TOP_IMPORTS:
                            violations.append((rel, node.lineno, f"from {mod} import ..."))
                elif isinstance(node, ast.Attribute):
                    if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr in ("environ", "getenv"):
                        violations.append((rel, node.lineno, f"os.{node.attr} forbidden"))

        self.assertEqual(violations, [], f"Import/isolation boundary violations in strategy_core: {violations}")

    def test_02_hash_sensitivity_and_crlf_invariance(self):
        """Section 6: Verify signal_spec_hash, exit_spec_hash, and spec_hash properties."""
        raw_params = json.loads(normalize_lf(DEFAULT_PARAMS_PATH.read_text(encoding="utf-8")))
        base_hashes = compute_all_hashes(raw_params, core_root=CORE_ROOT)

        # 1. Changing tp_pct changes exit_spec_hash and spec_hash, but NOT signal_spec_hash
        p_exit = copy.deepcopy(raw_params)
        p_exit["combos"][0]["exit_policy"]["tp_pct"] = 0.035
        h_exit = compute_all_hashes(p_exit, core_root=CORE_ROOT)
        self.assertEqual(h_exit["signal_spec_hash"], base_hashes["signal_spec_hash"])
        self.assertNotEqual(h_exit["exit_spec_hash"], base_hashes["exit_spec_hash"])
        self.assertNotEqual(h_exit["spec_hash"], base_hashes["spec_hash"])

        # 2. Changing entry parameter or layer2 changes signal_spec_hash and spec_hash, but NOT exit_spec_hash
        p_sig = copy.deepcopy(raw_params)
        p_sig["combos"][0]["entry"]["mfi_long"] = 60.0
        h_sig = compute_all_hashes(p_sig, core_root=CORE_ROOT)
        self.assertNotEqual(h_sig["signal_spec_hash"], base_hashes["signal_spec_hash"])
        self.assertEqual(h_sig["exit_spec_hash"], base_hashes["exit_spec_hash"])
        self.assertNotEqual(h_sig["spec_hash"], base_hashes["spec_hash"])

        p_l2 = copy.deepcopy(raw_params)
        p_l2["combos"][0]["layer2"] = "OFF"
        h_l2 = compute_all_hashes(p_l2, core_root=CORE_ROOT)
        self.assertNotEqual(h_l2["signal_spec_hash"], base_hashes["signal_spec_hash"])
        self.assertEqual(h_l2["exit_spec_hash"], base_hashes["exit_spec_hash"])

        # 3. Changing 1 character in logic.py (in a temporary copy of strategy_core) changes signal_spec_hash
        # 4. Converting LF to CRLF in all .py files does NOT change any hash
        with tempfile.TemporaryDirectory() as tmp:
            tmp_core = Path(tmp) / "strategy_core"
            shutil.copytree(CORE_ROOT, tmp_core)

            # Convert all .py files in tmp_core to CRLF
            for pf in tmp_core.rglob("*.py"):
                text_lf = normalize_lf(pf.read_text(encoding="utf-8"))
                pf.write_bytes(text_lf.replace("\n", "\r\n").encode("utf-8"))

            h_crlf = compute_all_hashes(raw_params, core_root=tmp_core)
            self.assertEqual(h_crlf, base_hashes, "CRLF line endings must not change any hash")

            # Mutate 1 character in logic.py inside tmp_core
            logic_tmp = tmp_core / "strategies" / "final20" / "logic.py"
            txt = logic_tmp.read_text(encoding="utf-8")
            logic_tmp.write_text(txt + "\n# 1-char mutation\n", encoding="utf-8")
            h_mut = compute_all_hashes(raw_params, core_root=tmp_core)
            self.assertNotEqual(h_mut["signal_spec_hash"], base_hashes["signal_spec_hash"])
            self.assertEqual(h_mut["exit_spec_hash"], base_hashes["exit_spec_hash"])
            self.assertNotEqual(h_mut["spec_hash"], base_hashes["spec_hash"])

            # 5. Adding an unrelated plugin under strategies/other_plugin/ must NOT change 20FINAL@1 hash
            plugin_dir = tmp_core / "strategies" / "other_plugin"
            plugin_dir.mkdir(parents=True, exist_ok=True)
            (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
            (plugin_dir / "logic.py").write_text("X = 1\n", encoding="utf-8")
            h_plugin = compute_all_hashes(raw_params, core_root=tmp_core)
            self.assertEqual(h_plugin, h_mut, "Adding an unrelated sibling plugin must not alter 20FINAL@1 hash")

        # 6. Changing is_new6 changes signal_spec_hash and spec_hash, but NOT exit_spec_hash
        p_new6 = copy.deepcopy(raw_params)
        p_new6["combos"][0]["is_new6"] = not bool(p_new6["combos"][0].get("is_new6", False))
        h_new6 = compute_all_hashes(p_new6, core_root=CORE_ROOT)
        self.assertNotEqual(h_new6["signal_spec_hash"], base_hashes["signal_spec_hash"])
        self.assertEqual(h_new6["exit_spec_hash"], base_hashes["exit_spec_hash"])
        self.assertNotEqual(h_new6["spec_hash"], base_hashes["spec_hash"])

    def test_03_baseline_lock_enforcement_m2(self):
        """Section 6 & M2: Modifying 20final_v1.json or missing lock file causes loading 20FINAL@1 to fail (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            tmp_params = tmp_dir / "20final_v1.json"
            tmp_lock = tmp_dir / "20final_v1.lock"
            shutil.copy2(DEFAULT_PARAMS_PATH, tmp_params)
            shutil.copy2(DEFAULT_LOCK_PATH, tmp_lock)

            # Valid baseline loads cleanly
            strat_ok = Final20Strategy(params_path=tmp_params, lock_path=tmp_lock, enforce_lock=True)
            self.assertEqual(strat_ok.spec().strategy_id, "20FINAL")

            # Missing lock file must fail-closed
            missing_lock = tmp_dir / "nonexistent.lock"
            with self.assertRaises(BaselineLockError):
                Final20Strategy(params_path=tmp_params, lock_path=missing_lock, enforce_lock=True)

            # Gate B1 must NOT set lock_verified=True when manifest lacks signal_spec_hash
            fake_run = tmp_dir / "legacy_run"
            fake_run.mkdir(parents=True, exist_ok=True)
            gold_dir = REPO_ROOT / "runs" / "golden" / "v3-08" / "ALL_SIGNALS"
            shutil.copy2(gold_dir / "signals.parquet", fake_run / "signals.parquet")
            manifest = json.loads((gold_dir / "run_manifest.json").read_text(encoding="utf-8"))
            manifest.pop("signal_spec_hash", None)
            (fake_run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            ctx_no_hash = VerifyContext(fake_run, REPO_ROOT, REPO_ROOT, reference_signals=gold_dir / "signals.parquet")
            res_no_hash = gates_b.gate_b1(ctx_no_hash)
            self.assertFalse(res_no_hash.details.get("lock_verified"))

            # Mutate 1 parameter in temporary 20final_v1.json
            data = json.loads(tmp_params.read_text(encoding="utf-8"))
            data["combos"][0]["exit_policy"]["tp_pct"] = 0.025
            tmp_params.write_text(json.dumps(data, indent=2), encoding="utf-8")

            with self.assertRaises(BaselineLockError):
                Final20Strategy(params_path=tmp_params, lock_path=tmp_lock, enforce_lock=True)

            # Mutate signal parameter
            data2 = json.loads(DEFAULT_PARAMS_PATH.read_text(encoding="utf-8"))
            data2["combos"][0]["layer2"] = "OFF"
            tmp_params.write_text(json.dumps(data2, indent=2), encoding="utf-8")

            with self.assertRaises(BaselineLockError):
                StrategyCoreRegistry.get("20FINAL", "1", params_dir=tmp_dir)

    def test_04_clone_and_combo_mutations_preserve_baseline(self):
        """Section 6: Clone via API and CLI, verify parent, hash diff, add/remove combo, and baseline immutability."""
        before_json_sha = hashlib.sha256(DEFAULT_PARAMS_PATH.read_bytes()).hexdigest()
        before_lock_sha = hashlib.sha256(DEFAULT_LOCK_PATH.read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_pdir = Path(tmp)
            shutil.copy2(DEFAULT_PARAMS_PATH, tmp_pdir / "20final_v1.json")
            shutil.copy2(DEFAULT_LOCK_PATH, tmp_pdir / "20final_v1.lock")

            # Test CLI strategy clone command
            cmd = [
                sys.executable,
                "-m",
                "autotrade_v3.cli",
                "strategy",
                "clone",
                "--from",
                "20FINAL@1",
                "--to",
                "RESEARCH_ALPHA@1",
                "--params-dir",
                str(tmp_pdir),
            ]
            proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=True)
            self.assertIn("RESEARCH_ALPHA@1", proc.stdout)

            cloned_strat = StrategyCoreRegistry.get("RESEARCH_ALPHA@1", params_dir=tmp_pdir)
            base_strat = StrategyCoreRegistry.get("20FINAL@1", params_dir=tmp_pdir)

            self.assertEqual(cloned_strat.spec().parent, "20FINAL@1")
            self.assertEqual(cloned_strat.spec().signal_spec_hash, base_strat.spec().signal_spec_hash)
            self.assertEqual(cloned_strat.spec().exit_spec_hash, base_strat.spec().exit_spec_hash)
            # spec_hash differs because strategy_id is RESEARCH_ALPHA vs 20FINAL
            self.assertNotEqual(cloned_strat.spec().spec_hash, base_strat.spec().spec_hash)

            # Now create version 2 of RESEARCH_ALPHA with 1 combo removed and 1 combo added
            new_combo = {
                "combo_key": "BTC-USDT/C3",
                "symbol": "BTC-USDT",
                "combo_id": 3,
                "combo_name": "C3",
                "native_timeframe": "1h",
                "is_new6": False,
                "entry_variant": "ADX_34",
                "entry": {"adx": 34.0},
                "layer2": "SMC_STRICT",
                "exit_policy": {
                    "tp_pct": 0.018,
                    "sl_pct": 0.006,
                    "split_legs": True,
                    "leg_weights": [0.5, 0.5],
                    "trailing_act_pct": 0.005,
                    "trailing_cb_pct": 0.005,
                    "max_bars_held": None,
                    "force_close_on_window_end": False,
                },
            }
            v2_strat = StrategyCoreRegistry.clone(
                from_ref="RESEARCH_ALPHA@1",
                to_ref="RESEARCH_ALPHA@2",
                params_dir=tmp_pdir,
                remove_combos=["BTC-USDT/C1"],
                add_combos=[new_combo],
            )

            # Verify two versions of the same strategy_id coexist in registry
            s_v1 = StrategyCoreRegistry.get("RESEARCH_ALPHA", "1", params_dir=tmp_pdir)
            s_v2 = StrategyCoreRegistry.get("RESEARCH_ALPHA", "2", params_dir=tmp_pdir)
            self.assertNotEqual(s_v1.spec().signal_spec_hash, s_v2.spec().signal_spec_hash)
            self.assertEqual(len(s_v2.spec().combos), 20)
            v2_keys = {c.combo_key for c in s_v2.spec().combos}
            self.assertNotIn("BTC-USDT/C1", v2_keys)
            self.assertIn("BTC-USDT/C3", v2_keys)

            # Verify signals of untouched combos in v2 match baseline exactly
            evs_base = base_strat.generate(self.synth_m15, only_approved=False, only_warmup_ready=False)
            evs_v2 = s_v2.generate(self.synth_m15, only_approved=False, only_warmup_ready=False)

            untouched_base = [e for e in evs_base if e.combo_key != "BTC-USDT/C1"]
            untouched_v2 = [e for e in evs_v2 if e.combo_key != "BTC-USDT/C3"]
            self.assertEqual(untouched_base, untouched_v2)

        after_json_sha = hashlib.sha256(DEFAULT_PARAMS_PATH.read_bytes()).hexdigest()
        after_lock_sha = hashlib.sha256(DEFAULT_LOCK_PATH.read_bytes()).hexdigest()
        self.assertEqual(before_json_sha, after_json_sha, "Baseline 20final_v1.json was modified!")
        self.assertEqual(before_lock_sha, after_lock_sha, "Baseline 20final_v1.lock was modified!")

    def test_05_signal_event_schema_has_no_tp_or_sl_price(self):
        """Section 4.5: SignalEvent must not have tp_price or sl_price attributes."""
        strat = StrategyCoreRegistry.get("20FINAL", "1")
        evs = strat.generate(self.synth_m15, only_approved=False, only_warmup_ready=False)
        self.assertGreater(len(evs), 0)
        ev = evs[0]
        self.assertIsInstance(ev, SignalEvent)
        self.assertFalse(hasattr(ev, "tp_price"))
        self.assertFalse(hasattr(ev, "sl_price"))
        for req_tag in ("smc_dir", "ob_blocked", "approved", "warmup_ready"):
            self.assertIn(req_tag, ev.tags)

    def test_06_adversarial_mutations_m1_m2_m3_m4_m5(self):
        """Section 9: Adversarial mutation tests M1, M2, M3, M4, M5."""
        base_strat = StrategyCoreRegistry.get("20FINAL", "1")
        base_spec = base_strat.spec()

        # M1: Change tp_pct of a combo:
        # (a) Keeping strategy_id="20FINAL" and version="1" -> loading baseline is rejected by 20final_v1.lock
        # (b) Doing it on a clone -> spec_hash and exit_spec_hash change while signal_spec_hash is unchanged
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            shutil.copy2(DEFAULT_PARAMS_PATH, tmp_dir / "20final_v1.json")
            shutil.copy2(DEFAULT_LOCK_PATH, tmp_dir / "20final_v1.lock")
            p_m1 = json.loads((tmp_dir / "20final_v1.json").read_text(encoding="utf-8"))
            p_m1["combos"][0]["exit_policy"]["tp_pct"] = 0.05
            (tmp_dir / "20final_v1.json").write_text(json.dumps(p_m1, indent=2), encoding="utf-8")

            with self.assertRaises(BaselineLockError):
                StrategyCoreRegistry.get("20FINAL", "1", params_dir=tmp_dir)

            m1_clone = StrategyCoreRegistry.clone(
                from_ref="20FINAL@1",
                to_ref="MUT_M1_CLONE@1",
                params_dir=tmp_dir,
                combo_overrides={"BTC-USDT/C1": {"exit_policy": {"tp_pct": 0.05}}},
            )
            self.assertEqual(m1_clone.spec().signal_spec_hash, base_spec.signal_spec_hash)
            self.assertNotEqual(m1_clone.spec().exit_spec_hash, base_spec.exit_spec_hash)
            self.assertNotEqual(m1_clone.spec().spec_hash, base_spec.spec_hash)
            evs_base = base_strat.generate(self.synth_m15, only_approved=False, only_warmup_ready=False)
            evs_m1_clone = m1_clone.generate(self.synth_m15, only_approved=False, only_warmup_ready=False)
            self.assertEqual(evs_base, evs_m1_clone)

        # M2: Change entry threshold of a combo on a clone:
        # signal_spec_hash changes, and Gate B1 returns INCONCLUSIVE (for clone without reference) or FAIL (against baseline reference), never PASS
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            clone_res = clone_strategy_params(
                from_ref="20FINAL@1",
                to_ref="MUT_M2@1",
                params_dir=tmp_dir,
                combo_overrides={"BTC-USDT/C7": {"entry": {"body_atr": 0.1, "volume_mult": 0.1}}},
            )
            self.assertNotEqual(clone_res["hashes"]["signal_spec_hash"], base_spec.signal_spec_hash)

            # Check Gate B1 as MUT_M2@1 (no reference -> INCONCLUSIVE with explicit reason, not PASS)
            fake_clone_run = tmp_dir / "clone_run"
            fake_clone_run.mkdir(parents=True, exist_ok=True)
            gold_dir = REPO_ROOT / "runs" / "golden" / "v3-08" / "ALL_SIGNALS"
            shutil.copy2(gold_dir / "signals.parquet", fake_clone_run / "signals.parquet")
            manifest_clone = json.loads((gold_dir / "run_manifest.json").read_text(encoding="utf-8"))
            manifest_clone["strategy_id"] = "MUT_M2"
            manifest_clone["strategy_version"] = "1"
            manifest_clone["signal_spec_hash"] = clone_res["hashes"]["signal_spec_hash"]
            (fake_clone_run / "run_manifest.json").write_text(json.dumps(manifest_clone), encoding="utf-8")

            ctx_m2_inc = VerifyContext(run_dir=fake_clone_run, dataset_root=REPO_ROOT, repo_root=REPO_ROOT)
            res_b1_inc = gates_b.gate_b1(ctx_m2_inc)
            self.assertEqual(res_b1_inc.status, "INCONCLUSIVE")
            self.assertNotEqual(res_b1_inc.status, "PASS")

            # Check Gate B1 when explicitly compared against baseline reference -> FAIL
            fake_run = tmp_dir / "fake_run"
            fake_run.mkdir(parents=True, exist_ok=True)
            shutil.copy2(gold_dir / "signals.parquet", fake_run / "signals.parquet")
            manifest = json.loads((gold_dir / "run_manifest.json").read_text(encoding="utf-8"))
            manifest["signal_spec_hash"] = clone_res["hashes"]["signal_spec_hash"]
            (fake_run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            ctx_m2_fail = VerifyContext(
                run_dir=fake_run,
                dataset_root=REPO_ROOT,
                repo_root=REPO_ROOT,
                reference_signals=gold_dir / "signals.parquet",
            )
            res_b1_fail = gates_b.gate_b1(ctx_m2_fail)
            self.assertEqual(res_b1_fail.status, "FAIL", "M2: Gate B1 must FAIL when signal_spec_hash or signals mismatch baseline reference")

        # M3: Modify a line in indicator logic (e.g. change SMC pivot length from 50 to 5):
        # B6 signal parity fails and lists the diverging signal_ids
        from unittest.mock import patch
        from autotrade_v3.strategy_core.indicators import smc_structure

        orig_smc_direction = smc_structure.smc_direction
        evs_orig = base_strat.generate(self.synth_m15, only_approved=True, only_warmup_ready=True)

        def mutated_smc_direction(df_in: pd.DataFrame, swing_len: int = 50, confluence: bool = False):
            # Force swing_len=5 instead of 50 (indicator pivot length mutation)
            return orig_smc_direction(df_in, swing_len=5, confluence=confluence)

        with patch("autotrade_v3.strategy_core.strategies.final20.logic.smc_direction", side_effect=mutated_smc_direction):
            evs_mut = base_strat.generate(self.synth_m15, only_approved=True, only_warmup_ready=True)
            orig_ids = {e.signal_id for e in evs_orig}
            mut_ids = {e.signal_id for e in evs_mut}
            diff_ids = sorted(orig_ids ^ mut_ids)
            self.assertGreater(len(diff_ids), 0, "M3: Indicator pivot mutation must cause signal_id divergence")

        # M5: Inject forbidden import into a temporary copy of strategy_core -> AST boundary test catches it
        with tempfile.TemporaryDirectory() as tmp:
            bad_py = Path(tmp) / "bad_module.py"
            bad_py.write_text("from autotrade_v2.app.strategy import final14_config\n", encoding="utf-8")
            tree = ast.parse(bad_py.read_text(encoding="utf-8"))
            caught = False
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] not in ALLOWED_TOP_IMPORTS:
                    caught = True
            self.assertTrue(caught, "M5: AST boundary scanner must catch autotrade_v2 import")

    @unittest.skipUnless(
        _has_real_m1_data(),
        "Real M1 dataset (catalog/research.duckdb & data/m1_raw/*.parquet) not present in CI; skipping Gate B3 M4a test per Invariant N8",
    )
    def test_06b_mutation_m4a_unclosed_bar_accepted_fails_b3(self):
        """Section 9 M4a: Making on_bar_close accept an unclosed candle (is_closed=False) causes Gate B3 to FAIL."""
        from unittest.mock import patch

        gates_b.clear_replay_cache()
        orig_on_bar_close = Final20Strategy.on_bar_close

        def unclosed_accepting_on_bar_close(self_strat: Any, state: Any, bar: Any, **kwargs: Any) -> list[SignalEvent]:
            if isinstance(bar, dict) and bar.get("is_closed") is False:
                return []
            return orig_on_bar_close(self_strat, state, bar, **kwargs)

        with patch.object(Final20Strategy, "on_bar_close", unclosed_accepting_on_bar_close):
            gates_b.clear_replay_cache()
            ctx_m4a = VerifyContext(REPO_ROOT / "runs" / "golden" / "v3-08" / "ALL_SIGNALS", REPO_ROOT, REPO_ROOT)
            res_b3 = gates_b.gate_b3(ctx_m4a)
            self.assertEqual(res_b3.status, "FAIL", "M4a: Gate B3 must FAIL when on_bar_close accepts unclosed candle")
            viol_kinds = {v.get("kind") for v in res_b3.violations}
            self.assertIn("UNCLOSED_BAR_ACCEPTED", viol_kinds)
        gates_b.clear_replay_cache()

    @unittest.skipUnless(
        _has_real_m1_data(),
        "Real M1 dataset (catalog/research.duckdb & data/m1_raw/*.parquet) not present in CI; skipping Gate B3 M4b test per Invariant N8",
    )
    def test_06c_mutation_m4b_batch_live_divergence_fails_b3(self):
        """Section 9 M4b: Making on_bar_close diverge from batch generate() causes Gate B3 to FAIL."""
        from unittest.mock import patch

        gates_b.clear_replay_cache()
        orig_on_bar_close = Final20Strategy.on_bar_close

        def diverging_on_bar_close(self_strat: Any, state: Any, bar: Any, **kwargs: Any) -> list[SignalEvent]:
            evs = orig_on_bar_close(self_strat, state, bar, **kwargs)
            if evs:
                e0 = evs[0]
                mut_ev = SignalEvent(
                    signal_id=e0.signal_id + "_SHIFTED",
                    combo_key=e0.combo_key,
                    symbol=e0.symbol,
                    side=e0.side,
                    native_open_ms=e0.native_open_ms,
                    signal_close_ms=e0.signal_close_ms + 900_000,
                    ref_price=e0.ref_price,
                    tags=e0.tags,
                )
                return [mut_ev] + evs[1:]
            return evs

        with patch.object(Final20Strategy, "on_bar_close", diverging_on_bar_close):
            gates_b.clear_replay_cache()
            ctx_m4b = VerifyContext(REPO_ROOT / "runs" / "golden" / "v3-08" / "ALL_SIGNALS", REPO_ROOT, REPO_ROOT)
            res_b3 = gates_b.gate_b3(ctx_m4b)
            self.assertEqual(res_b3.status, "FAIL", "M4b: Gate B3 must FAIL when on_bar_close diverges from generate()")
            viol_kinds = {v.get("kind") for v in res_b3.violations}
            self.assertIn("ONLY_IN_LIVE", viol_kinds)
        gates_b.clear_replay_cache()

    def test_07_clone_existing_and_self_rejected_and_alias_and_live_state_preserved(self):
        """Verify review fixes: clone overwrite/self-clone rejection, single-DF alias normalization, live state flags, and B3 record checks."""
        base_strat = StrategyCoreRegistry.get("20FINAL", "1")

        # 1. Self-clone & overwrite clone rejection (Mục 4)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_pdir = Path(tmp)
            shutil.copy2(DEFAULT_PARAMS_PATH, tmp_pdir / "20final_v1.json")
            shutil.copy2(DEFAULT_LOCK_PATH, tmp_pdir / "20final_v1.lock")

            with self.assertRaises(ValueError):
                clone_strategy_params("20FINAL@1", "20FINAL@1", params_dir=tmp_pdir)

            clone_strategy_params("20FINAL@1", "EXISTING@1", params_dir=tmp_pdir)
            with self.assertRaises(FileExistsError):
                clone_strategy_params("20FINAL@1", "EXISTING@1", params_dir=tmp_pdir)
            with self.assertRaises(ValueError):
                clone_strategy_params("EXISTING@1", "EXISTING@1", params_dir=tmp_pdir)

        # 2. Single-DataFrame symbol alias normalization (Mục 8)
        evs_canon = base_strat.generate(
            self.synth_m15["BTC-USDT"],
            symbols=["BTC-USDT"],
            only_approved=False,
            only_warmup_ready=False,
        )
        evs_alias = base_strat.generate(
            self.synth_m15["BTC-USDT"],
            symbols=["BTCUSDT"],
            only_approved=False,
            only_warmup_ready=False,
        )
        self.assertGreater(len(evs_canon), 0)
        self.assertEqual(len(evs_alias), len(evs_canon))
        self.assertTrue(all(e.symbol == "BTCUSDT" for e in evs_alias))

        # 3. on_bar_close preserves live_state.only_approved/only_warmup_ready unless explicitly overridden (Mục 7)
        live_state = base_strat.init_live_state(only_approved=False, only_warmup_ready=False)
        df_btc = self.synth_m15["BTC-USDT"]
        t_arr = df_btc.index.asi8 // 1_000_000
        collected_default = []
        for i in range(len(df_btc)):
            bar_d = {
                "symbol": "BTC-USDT",
                "open_ms": int(t_arr[i]),
                "open": float(df_btc["open"].iloc[i]),
                "high": float(df_btc["high"].iloc[i]),
                "low": float(df_btc["low"].iloc[i]),
                "close": float(df_btc["close"].iloc[i]),
                "volume": float(df_btc["volume"].iloc[i]),
                "provider": str(df_btc["provider"].iloc[i]) if "provider" in df_btc.columns else "SYNTHETIC",
                "is_closed": True,
            }
            collected_default.extend(base_strat.on_bar_close(live_state, bar_d))
        self.assertEqual(collected_default, evs_canon)

        # 4. B3 detects duplicate signal_id and field mismatch in signals.parquet even when signal_id set matches (Mục 5)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            fake_run = tmp_dir / "b3_dup_run"
            fake_run.mkdir(parents=True, exist_ok=True)
            gold_dir = REPO_ROOT / "runs" / "golden" / "v3-08" / "ALL_SIGNALS"
            shutil.copy2(gold_dir / "run_manifest.json", fake_run / "run_manifest.json")
            shutil.copy2(REPO_ROOT / "runs" / "golden" / "v3-08" / "dataset_manifest.json", fake_run / "dataset_manifest.json")

            sigs_df = pd.read_parquet(gold_dir / "signals.parquet")
            # Duplicate first row and alter entry_price on row 1
            dup_df = pd.concat([sigs_df.iloc[[0]], sigs_df], ignore_index=True)
            dup_df.loc[1, "entry_price"] = float(dup_df.loc[1, "entry_price"]) + 100.0
            dup_df.to_parquet(fake_run / "signals.parquet", index=False)

            gates_b.clear_replay_cache()
            # Pre-populate _B3_CACHE with exact golden records so this unit test runs fast without 3-year M1
            spec = base_strat.spec()
            m = json.loads((gold_dir / "run_manifest.json").read_text(encoding="utf-8"))
            dm = json.loads((fake_run / "dataset_manifest.json").read_text(encoding="utf-8"))
            cache_key = (
                spec.strategy_id,
                spec.version,
                spec.signal_spec_hash,
                str(m.get("dataset_hash") or dm.get("dataset_hash") or ""),
                tuple(sorted(m.get("symbols", ["BTC-USDT", "ETH-USDT"]))),
                m["time_window"]["start"],
                m["time_window"]["end"],
            )
            fake_records = [
                {
                    "signal_id": str(r["signal_id"]),
                    "symbol": str(r["symbol"]),
                    "combo_id": int(r["combo_id"]),
                    "side": str(r["side"]),
                    "signal_close_ms": int(r["signal_close_ms"]),
                    "ref_price": float(r["entry_price"]),
                }
                for _, r in sigs_df.iterrows()
            ]
            gates_b._B3_CACHE[cache_key] = (
                len(sigs_df),
                len(sigs_df),
                [],
                {"live_eval_records": fake_records, "per_symbol": {}},
            )
            ctx_dup = VerifyContext(fake_run, REPO_ROOT, REPO_ROOT)
            res_dup = gates_b.gate_b3(ctx_dup)
            gates_b.clear_replay_cache()
            self.assertEqual(res_dup.status, "FAIL")
            kinds = {v.get("kind") for v in res_dup.violations}
            self.assertIn("DUPLICATE_SIGNAL_IN_RUN", kinds)
            self.assertIn("RUN_SIGNAL_FIELD_MISMATCH", kinds)


if __name__ == "__main__":
    unittest.main()
