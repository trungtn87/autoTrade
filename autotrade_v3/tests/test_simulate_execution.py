"""V3-03 deterministic offline execution smoke & edge cases."""
from __future__ import annotations
import csv
import json
import tempfile
import unittest
from pathlib import Path

from autotrade_v3.data_backtest.simulate_execution import (
    Candle, Profile, simulate_event, simulate_one, load_events, _mark_merged,
    _leg_plan, _quantize_qty, run,
)

T=1_700_000_040_000  # full minute boundary

def bar(i, o=100, hi=100, lo=100, c=100, provider='BINANCE'):
    return Candle(T+i*60_000, float(o), float(hi), float(lo), float(c), provider)

def signal(side='BUY', combo='C1', combo_id=1):
    return dict(symbol='BTC-USDT',combo=combo,combo_id=str(combo_id),side=side,
                signal_close_ms=str(T-1),approved='true',warmup_ready='true')

class ExecutionScenariosTest(unittest.TestCase):
    def profile(self, **kw):
        return Profile(exit_mode='hard_tp', fee_bps_per_side=0,
                       half_spread_bps=0, entry_slippage_bps=0,
                       exit_slippage_bps=0, **kw)

    def simulate(self, candles, profile=None, side='BUY', tp=.02, sl=.01):
        p=profile or self.profile()
        return simulate_event(signal(side),candles,[x.t for x in candles],p,tp,sl)

    def test_long_hard_tp_and_full_close(self):
        candles=[bar(0),bar(1,100,103,100,102)]
        out=self.simulate(candles)
        self.assertTrue(out['closed_in_both'])
        self.assertEqual(out['scenario_quality'],'SIMULATED')
        self.assertAlmostEqual(out['high_first']['net_usdt'],2.0,places=5)
        self.assertEqual(out['low_first']['legs'][0]['exit_reason'],'TP')
        self.assertEqual(out['high_first']['exit_t'],T+60_000)

    def test_same_m1_tp_sl_reports_ambiguity(self):
        out=self.simulate([bar(0),bar(1,100,103,98,100)])
        self.assertEqual(out['scenario_quality'],'AMBIGUOUS')
        self.assertTrue(out['closed_in_both'])
        self.assertAlmostEqual(out['scenario_net_min_usdt'],-1.0,places=5)
        self.assertAlmostEqual(out['scenario_net_max_usdt'],2.0,places=5)
        self.assertIn('M1_PATH_AMBIGUOUS',out['flags'])

    def test_short_same_m1_uncertainty(self):
        out=self.simulate([bar(0),bar(1,100,103,98,100)],side='SELL')
        self.assertEqual(out['scenario_quality'],'AMBIGUOUS')
        self.assertAlmostEqual(out['scenario_net_min_usdt'],-1.0,places=5)
        self.assertAlmostEqual(out['scenario_net_max_usdt'],2.0,places=5)

    def test_split_tp_trailing_exit_same_bar_can_differ(self):
        p=Profile(exit_mode='split_trailing',notional_usdt=100,
                  fee_bps_per_side=0,entry_slippage_bps=0,
                  exit_slippage_bps=0,half_spread_bps=0,
                  trailing_activation_pct=.005,trailing_callback_rate=.005)
        candles=[bar(0),bar(1,100,103,99,100),bar(2,100,104,99,103)]
        out=self.simulate(candles,p)
        self.assertTrue(all(len(out[x]['legs'])==2 for x in ('high_first','low_first')))
        self.assertEqual(out['high_first']['legs'][0]['exit_reason'],'TP')
        self.assertTrue(out['high_first']['legs'][1]['exit_reason'] in ('TRAILING','SL','SL_GAP','TRAILING_GAP'))
        self.assertEqual(out['scenario_quality'],'AMBIGUOUS')

    def test_hard_tp_does_not_claim_nonexistent_trailing_activation(self):
        out=self.simulate([bar(0,100,100.6,99.8,100),bar(1,100,100.2,99.8,100)])
        self.assertNotIn('ENTRY_MINUTE_TRAILING_ACTIVATION_UNOBSERVABLE',out['flags'])
        self.assertEqual(out['scenario_quality'],'SIMULATED')

    def test_short_split_trailing_activation(self):
        p=Profile(exit_mode='split_trailing',fee_bps_per_side=0,
                  entry_slippage_bps=0,exit_slippage_bps=0,half_spread_bps=0)
        candles=[bar(0),bar(1,100,100.1,97,99.5),bar(2,99.5,101,97,100)]
        out=self.simulate(candles,p,side='SELL')
        self.assertTrue(any(x['name']=='TRAIL' for x in out['high_first']['legs']))
        self.assertTrue(any(x['name']=='TRAIL' for x in out['low_first']['legs']))

    def test_worse_sl_gap_executed_at_open_not_stop(self):
        out=self.simulate([bar(0),bar(1,95,96,94,95)])
        self.assertTrue(out['closed_in_both'])
        self.assertAlmostEqual(out['scenario_net_max_usdt'],-5,places=5)
        self.assertEqual(out['high_first']['legs'][0]['exit_reason'],'SL_GAP')

    def test_fee_spread_slippage_decreases_pnl(self):
        clean=self.simulate([bar(0),bar(1,100,103,100,102)])
        expensive=self.simulate([bar(0),bar(1,100,103,100,102)],
             Profile(exit_mode='hard_tp',fee_bps_per_side=5,
                     half_spread_bps=2,entry_slippage_bps=3,exit_slippage_bps=4))
        self.assertLess(expensive['high_first']['net_usdt'],clean['high_first']['net_usdt'])
        self.assertGreater(expensive['high_first']['fees_usdt'],0)
        self.assertLess(expensive['high_first']['qty'],clean['high_first']['qty'])

    def test_latency_uses_next_eligible_open_not_signal_candle(self):
        p=self.profile(entry_latency_ms=30_000)
        candles=[bar(0),bar(1,102,102,102,102),bar(2,102,105,102,104)]
        result=simulate_one(signal(),candles,[x.t for x in candles],p,.02,.01,'HIGH_FIRST')
        self.assertEqual(result.entry_t,T+60_000)
        self.assertAlmostEqual(result.entry_price,102)
        self.assertIn('QTY', ''.join(result.flags)) if result.qty <= 0 else None

    def test_entry_minute_extremes_are_not_fabricated_fills(self):
        out=self.simulate([bar(0,100,105,95,100),bar(1,100,100.5,99.5,100)])
        self.assertFalse(out['closed_in_both'])
        self.assertIn('ENTRY_MINUTE_TP_SL_ORDERING_UNOBSERVABLE',out['flags'])
        self.assertIsNone(out['high_first']['net_usdt'])

    def test_open_end_has_no_fake_roi(self):
        out=self.simulate([bar(0),bar(1,100,100.2,99.9,100)])
        self.assertFalse(out['closed_in_both'])
        self.assertIsNone(out['scenario_net_min_usdt'])
        self.assertEqual(out['high_first']['status'],'OPEN_END_OR_CENSORED')

    def test_zero_fill_for_tiny_notional(self):
        p=Profile(notional_usdt=.00001)
        out=self.simulate([bar(0),bar(1)],p)
        self.assertEqual(out['high_first']['status'],'NO_ENTRY')

    def test_split_falls_back_if_quantity_too_small(self):
        legs, mode, flags = _leg_plan(.0001,Profile())
        self.assertEqual(mode,'hard_tp')
        self.assertEqual(len(legs),1)
        self.assertTrue(flags)

    def test_gap_in_open_exposure_rejected(self):
        candles=[bar(0),bar(2,100,100,99,99)]
        with self.assertRaisesRegex(ValueError,'noncontinuous M1'):
            simulate_one(signal(),candles,[x.t for x in candles],self.profile(),.02,.01,'HIGH_FIRST')

    def test_negative_parameter_rejected(self):
        with self.assertRaises(ValueError):
            Profile(entry_latency_ms=-1)
        with self.assertRaises(ValueError):
            Profile(fee_bps_per_side=-1)
        with self.assertRaises(ValueError):
            Profile(exit_mode='unknown')

    def test_no_live_export_does_not_mean_live_verified(self):
        out=self.simulate([bar(0),bar(1,100,103,100,102)])
        self.assertEqual(out['evidence_level'],'SIMULATED_NOT_BINGX_VERIFIED')

    def test_shared_same_side_is_marked_not_blocked(self):
        a=self.simulate([bar(0),bar(1,100,103,100,102)])
        b=self.simulate([bar(0),bar(1,100,103,100,102)])
        b['combo']='C2';b['combo_id']=2
        meta=_mark_merged([a,b])
        self.assertEqual(meta['trades_touching_shared_container'],2)
        self.assertTrue(a['shared_container_overlap'])

    def test_trailing_leg_after_tp_then_full_sl(self):
        # TP closes 50% at 102; remainder can still stop separately.
        p=Profile(exit_mode='split_trailing',fee_bps_per_side=0,
                  entry_slippage_bps=0,exit_slippage_bps=0,half_spread_bps=0,
                  trailing_activation_pct=.005,trailing_callback_rate=.5)
        c=[bar(0),bar(1,100,102,100,102),bar(2,102,102,98,99)]
        out=self.simulate(c,p,sl=.01)
        self.assertTrue(out['closed_in_both'])
        names=[x['name'] for x in out['high_first']['legs']]
        self.assertEqual(names,['TP','TRAIL'])
        self.assertEqual(out['high_first']['legs'][0]['exit_reason'],'TP')
        self.assertIn(out['high_first']['legs'][1]['exit_reason'],('SL','SL_GAP'))

    def test_max_holding_is_censored_not_closed(self):
        p=self.profile(max_holding_minutes=2)
        c=[bar(i,100,100.1,99.9,100) for i in range(8)]
        out=self.simulate(c,p)
        self.assertFalse(out['closed_in_both'])
        self.assertIn('MAX_HOLDING_CENSORED_NOT_FORCED_EXIT',out['flags'])
        self.assertIsNone(out['high_first']['estimated_roi_on_margin_pct'])

    def test_mark_proxy_liquidation_flag(self):
        p=self.profile()
        c=[bar(0),bar(1,100,100,97,99)]
        out=self.simulate(c,p)
        self.assertIn('APPROX_LIQUIDATION_THRESHOLD_WICK_MARK_BASIS_UNKNOWN',out['flags'])
        self.assertEqual(out['scenario_quality'],'AMBIGUOUS')

    def test_funding_cashflow_is_assumption(self):
        # A UTC scheduled 8h funding tick occurs while the position is still open.
        t0=28_800_000-120_000
        candles=[Candle(t0+i*60_000,100,100.0,100.0,100.0,'BINANCE') for i in range(4)]
        e=signal();e['signal_close_ms']=str(t0-1)
        p=self.profile(funding_rate_per_8h=.001)
        result=simulate_one(e,candles,[x.t for x in candles],p,.02,.01,'HIGH_FIRST')
        self.assertLess(result.funding_usdt,0)
        e['side']='SELL'
        short=simulate_one(e,candles,[x.t for x in candles],p,.02,.01,'HIGH_FIRST')
        self.assertGreater(short.funding_usdt,0)

    def test_roi_is_estimate_not_exchange_verified(self):
        out=self.simulate([bar(0),bar(1,100,103,100,102)])
        self.assertAlmostEqual(out['high_first']['estimated_roi_on_margin_pct'],100.0)
        self.assertEqual(out['trigger_basis'],'M1_LAST_PROXY_NOT_NATIVE_MARK_OR_TICK')

    def test_offline_end_to_end_report_and_no_ambiguity_upgrade(self):
        # The V3 batch simulator can operate on a compact read-only DuckDB M1 fixture.
        import duckdb
        from datetime import datetime,timezone
        start=int(datetime(2026,10,1,tzinfo=timezone.utc).timestamp()*1000)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=root/'raw.duckdb'
            con=duckdb.connect(str(db))
            con.execute("""CREATE TABLE v3_m1 AS
             SELECT symbol, ?::BIGINT + i * 60000::BIGINT AS open_ms,
               100.0 AS open, 103.0 AS high, 98.0 AS low, 100.0 AS "close",
               1.0 AS volume,'BINANCE' AS provider
             FROM (SELECT 'BTCUSDT' AS symbol UNION ALL SELECT 'ETHUSDT' AS symbol) s,
                  range(8) t(i)""",[start])
            con.close()
            events=root/'signal_events.csv'
            items=[]
            for symbol in ('BTC-USDT','ETH-USDT'):
                r=signal();r['symbol']=symbol;r['signal_close_ms']=str(start+60000-1)
                items.append(r)
            with events.open('w',newline='') as file:
                w=csv.DictWriter(file,fieldnames=list(items[0]))
                w.writeheader();w.writerows(items)
            result=run(events,db,root/'out',self.profile(),
                       start='2026-10-01',end='2026-10-02')
            self.assertEqual(result['total_entries_simulated'],2)
            self.assertTrue((root/'out'/'scenario_trades.jsonl').exists())
            self.assertTrue((root/'out'/'execution_report.json').exists())
            self.assertEqual(result['gate'],'C1')
            self.assertEqual(result['status'],'RESEARCH_SCENARIOS_NOT_BINGX_CALIBRATED')
            self.assertEqual(result['shared_position_risk']['shared_container_pairs'],0)

    def test_cannot_mistake_duplicate_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'signal_events.csv'
            with p.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(signal().keys()))
                w.writeheader();w.writerow(signal());w.writerow(signal())
            with self.assertRaisesRegex(ValueError,'duplicate approved'):
                load_events(p)

if __name__=='__main__':unittest.main()
