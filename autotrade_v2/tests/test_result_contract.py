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
    def assert_transition_rejected(self, previous, candidate):
        # Both snapshots are independently valid: rejection must be temporal.
        before, after = validate_result(previous), validate_result(candidate)
        with self.assertRaises(InvalidResult):
            validate_transition(before, after)

    def test_complete_state_transition_matrix(self):
        states = {
            'UNRESOLVED': snapshot(status='UNRESOLVED'),
            'OPEN': snapshot(),
            'PARTIAL': snapshot(status='PARTIAL', closed_qty='1', exit_types=['TP']),
            'CLOSED': snapshot(status='CLOSED', closed_qty='2', closed_ms=2000, exit_types=['TP']),
        }
        allowed = {'UNRESOLVED': {'UNRESOLVED','OPEN','PARTIAL','CLOSED'},
                   'OPEN': {'OPEN','PARTIAL','CLOSED'},
                   'PARTIAL': {'PARTIAL','CLOSED'}, 'CLOSED': {'CLOSED'}}
        for old_status, before in states.items():
            for new_status, after in states.items():
                candidate = {**after, 'exit_types':sorted(set(before['exit_types'] + after['exit_types'])),
                             'evidence_refs':before['evidence_refs'] + ['new-observation']}
                with self.subTest(before=old_status, after=new_status):
                    if new_status in allowed[old_status]:
                        validate_transition(validate_result(before), validate_result(candidate))
                    else:
                        self.assert_transition_rejected(before, candidate)

    def test_partial_cannot_regress_quantity_with_fresh_or_correction_evidence(self):
        before = snapshot(status='PARTIAL', closed_qty='1', exit_types=['TP'])
        for changes in ({'status':'OPEN','closed_qty':'0'}, {'closed_qty':'0.5'},
                        {'opened_qty':'1.5'}, {'closed_qty':'0.99999999999999999999'}):
            candidate = {**before, **changes, 'evidence_refs':['entry-1','correction-proof'],
                         'correction':{'reason':'provider correction', 'approved':True}}
            self.assert_transition_rejected(before, candidate)
        # Unknown status does not authorize erasing previously observed fills.
        unresolved = {**before, 'status':'UNRESOLVED'}
        for key in ('opened_qty','closed_qty'):
            self.assert_transition_rejected(unresolved, {**unresolved, key:None})

    def test_status_guard_is_independent_of_quantity_and_money_validation(self):
        for before, targets in (
            (snapshot(status='CLOSED', closed_qty='2', closed_ms=2000, exit_types=['TP']),
             ('UNRESOLVED','OPEN','PARTIAL')),
            (snapshot(status='PARTIAL', closed_qty='1', exit_types=['TP']), ('OPEN','UNRESOLVED')),
        ):
            for target in targets:
                # Preserve every fact: status guard itself must reject this.
                with self.assertRaisesRegex(InvalidResult, 'status_regression_correction_required'):
                    validate_transition(before, {**before, 'status':target})

    def test_closed_quantity_and_timestamps_are_frozen(self):
        before = snapshot(status='CLOSED', closed_qty='2', closed_ms=2000, exit_types=['TP'])
        for changes in ({'opened_qty':'3','closed_qty':'3'}, {'opened_qty':'1','closed_qty':'1'},
                        {'opened_ms':900}, {'closed_ms':3000}, {'exit_types':['SL']}):
            self.assert_transition_rejected(before, {**before, **changes,
                'evidence_refs':['entry-1','new-proof']})

    def test_progress_requires_new_evidence_but_numeric_reencoding_does_not(self):
        before = snapshot()
        self.assert_transition_rejected(before, {**before,'status':'PARTIAL','closed_qty':'1','exit_types':['TP']})
        validate_transition(validate_result(before), validate_result({**before, 'opened_qty':2.0,'closed_qty':0}))
        partial = {**before, 'status':'PARTIAL','closed_qty':'1','exit_types':['TP'],
                   'evidence_refs':['entry-1','fill-1']}
        validate_transition(validate_result(before), validate_result(partial))
        larger = {**partial, 'opened_qty':'3','closed_qty':'2',
                  'evidence_refs':partial['evidence_refs'] + ['entry-fill-2','exit-fill-2']}
        validate_transition(validate_result(partial), validate_result(larger))

    def test_verified_money_result_and_provenance_cannot_be_replaced(self):
        before = snapshot(status='CLOSED', closed_qty='2', closed_ms=2000, exit_types=['TP'],
            pnl='1', fees='0.1', margin='10', roi='10', outcome='WIN', money_verified=True,
            money_evidence_refs=['money-1'], margin_evidence_ref='margin-1',
            evidence_refs=['entry-1','money-1','money-2','margin-1','margin-2'])
        for changes in ({'pnl':'2','roi':'20'}, {'fees':'0.2'}, {'margin':'20','roi':'5'},
                        {'outcome':None}, {'roi':None}, {'money_evidence_refs':['money-2']},
                        {'margin_evidence_ref':'margin-2'},
                        {'pnl':'-1','roi':'-10','outcome':'LOSS'}):
            self.assert_transition_rejected(before, {**before, **changes})
        self.assert_transition_rejected(before, {**before,'verification_source':'UNVERIFIED'})
        pending = snapshot(money_verified=True, money_evidence_refs=['money-1'],
                           evidence_refs=['entry-1','money-1'])
        self.assert_transition_rejected(pending, {**pending,'money_verified':False})
        self.assert_transition_rejected(pending, {**pending,'money_evidence_refs':None})
        zero = {**before,'pnl':0,'fees':0,'roi':0,'outcome':'BREAKEVEN'}
        self.assert_transition_rejected(zero, {**zero,'pnl':'1','roi':'10','outcome':'WIN'})
        self.assert_transition_rejected(zero, {**zero,'fees':None,'pnl':None,'roi':None,'outcome':None})
        # NULL -> verified result is enrichment; it cannot mutate a known result.
        unknown = snapshot(status='CLOSED', closed_qty='2', closed_ms=2000, exit_types=['TP'])
        validate_transition(validate_result(unknown), validate_result(before))
        validate_transition(validate_result(before), validate_result({**before,'pnl':1.0,'fees':0.1,'margin':10,'roi':10}))

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
        self.assert_invalid(snapshot(status='CLOSED', opened_qty='0.0000000001', closed_qty='0',
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
