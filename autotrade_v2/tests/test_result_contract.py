"""Pure evidence contract tests; no execution or provider network."""
import copy
import unittest
from autotrade_v2.analytics.contract import InvalidResult, validate_result, validate_transition


def snapshot(**changes):
    row = dict(contract_version=1, event_id='intent-1', symbol='BTC-USDT', combo='C1',
               side='BUY', entry_order_id='entry-1', opened_ms=1000, closed_ms=None,
               status='OPEN', outcome=None, exit_types=[], opened_qty='2', closed_qty='0',
               pnl=None, fees=None, margin=None, roi=None,
               verification_source='V2_CONFIRMED_ORDER', evidence_refs=['entry-1'])
    row.update(changes)
    return row


class ResultContractTests(unittest.TestCase):
    def test_open_and_partial_preserve_nulls(self):
        for row in (snapshot(), snapshot(status='PARTIAL', closed_qty='1', exit_types=['TP'])):
            result = validate_result(row)
            self.assertIsNone(result['pnl'])
            self.assertIsNone(result['roi'])
            self.assertIsNone(result['outcome'])
            result['evidence_refs'].append('detached')
            self.assertEqual(row['evidence_refs'], ['entry-1'])

    def test_tp_sl_split_and_mixed_are_one_snapshot(self):
        for kinds in (['TP'], ['SL'], ['TP', 'TRAILING'], ['TP', 'SL']):
            row = validate_result(snapshot(status='CLOSED', closed_ms=2000,
                                           closed_qty='2', exit_types=kinds))
            self.assertEqual(row['event_id'], 'intent-1')
            self.assertIsNone(row['outcome'])

    def test_partial_entry_cannot_claim_more_closure(self):
        self.assert_invalid(snapshot(status='CLOSED', opened_qty='1', closed_qty='2',
                                     closed_ms=2000, exit_types=['TP']))

    def test_missing_legacy_combo_is_unresolved_and_margin_stays_null(self):
        row = validate_result(snapshot(status='UNRESOLVED', combo=None, opened_ms=None,
                                       opened_qty=None, closed_qty=None))
        self.assertIsNone(row['combo'])
        self.assertIsNone(row['margin'])
        self.assert_invalid({**row, 'status':'CLOSED', 'closed_ms':2000})

    def test_exit_kind_does_not_decide_outcome(self):
        for kind, pnl, outcome in (('TP', '-1', 'LOSS'), ('SL', '1', 'WIN'), ('SL', '0', 'BREAKEVEN')):
            row = snapshot(status='CLOSED', closed_ms=2000, closed_qty='2', exit_types=[kind],
                           pnl=pnl, fees='0.1', outcome=outcome, money_verified=True,
                           evidence_refs=['entry-1', 'money-1'], money_evidence_refs=['money-1'])
            self.assertEqual(validate_result(row)['outcome'], outcome)
            self.assert_invalid({**row, 'outcome': 'WIN' if outcome != 'WIN' else 'LOSS'})
            self.assert_invalid({**row, 'money_verified':False})
            self.assert_invalid({**row, 'fees':None})
            self.assert_invalid({**row, 'money_evidence_refs':['unattributed']})

    def test_roi_requires_actual_margin_and_net_money(self):
        row = snapshot(status='CLOSED', closed_ms=2000, closed_qty='2', exit_types=['SL'],
                       pnl='-1', fees='0.1', money_verified=True, margin='10', roi='-10',
                       margin_evidence_ref='margin-1', money_evidence_refs=['money-1'],
                       evidence_refs=['entry-1', 'margin-1', 'money-1'])
        self.assertEqual(validate_result(row)['roi'], '-10')
        for changes in ({'margin':None}, {'margin':'0'}, {'roi':'10'},
                        {'margin_evidence_ref':None}, {'pnl':None}):
            self.assert_invalid({**row, **changes})

    def test_missing_field_is_not_silently_zero(self):
        row = snapshot()
        del row['fees']
        self.assert_invalid(row)

    def test_transition_cannot_erase_facts_or_replace_identity(self):
        previous = snapshot(status='CLOSED', closed_qty='2', closed_ms=2000, exit_types=['TP'])
        for changes in ({'symbol':'ETH-USDT'}, {'side':'SELL'}, {'entry_order_id':'different'},
                        {'combo':'C2'}, {'status':'OPEN'}, {'evidence_refs':['unrelated']}):
            with self.assertRaises(InvalidResult):
                validate_transition(previous, {**previous, **changes})
        for key in ('pnl','fees','margin'):
            with self.assertRaises(InvalidResult):
                validate_transition({**previous, key:'1'}, previous)

    def test_bad_inputs_and_premature_finalization(self):
        for changes in ({'closed_qty':-1}, {'opened_qty':'NaN'}, {'pnl':float('inf')},
                        {'contract_version':True}, {'opened_ms':True}, {'status':{}},
                        {'exit_types':[{}]}, {'exit_types':['TP','TP']},
                        {'closed_ms':2000}, {'outcome':'WIN'}, {'closed_qty':'1'},
                        {'status':'PARTIAL'}, {'evidence_refs':[]}, {'source':'different'}):
            with self.subTest(changes=changes):
                self.assert_invalid(snapshot(**changes))

    def assert_invalid(self, row):
        before = copy.deepcopy(row)
        with self.assertRaises(InvalidResult):
            validate_result(row)
        self.assertEqual(row, before)
