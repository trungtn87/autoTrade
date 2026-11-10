"""BingX read-only saved GET JSON normalizer tests."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from autotrade_v3.data_backtest.normalize_bingx_history import normalize

class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p=Path(self.tmp.name)
        self.orders=self.p/"orders.json"
        self.fills=self.p/"fills.json"
        self.out=self.p/"normal"
        self.orders_obj={"code":0,"data":{"orders":[{
            "symbol":"BTC-USDT","orderID":"90071992547409998765","side":"SELL",
            "positionSide":"SHORT","type":"MARKET","status":"FILLED",
            "positionID":"p1","executedQty":"1","workingType":"MARK_PRICE",
            "apiSecret":"do_not_copy"}]}}
        self.fills_obj={"code":0,"data":{"fill_orders":[{
            "symbol":"BTC-USDT","tradeId":"90071992547409999999","orderId":"90071992547409998765",
            "side":"SELL","positionSide":"SHORT","qty":"1","price":"101",
            "realizedPnl":"0","fee":"-0.03","time":1793145600000}]}}
    def write(self):
        self.orders.write_text(json.dumps(self.orders_obj))
        self.fills.write_text(json.dumps(self.fills_obj))
    def test_over_2_pow_53_id_and_secret_never_copied(self):
        self.write()
        result=normalize(self.orders,self.fills,self.out)
        self.assertEqual(result["orders"],1)
        with (self.out/"orders.csv").open() as f:
            row=list(csv.DictReader(f))[0]
        self.assertEqual(row["order_id"],"90071992547409998765")
        self.assertEqual(row["working_type"],"MARK_PRICE")
        self.assertNotIn("apiSecret",row)
        self.assertNotIn("do_not_copy",(self.out/"orders.csv").read_text())
        self.assertEqual(result["pagination_completeness"],"NOT_ATTESTED")
    def test_nonzero_error_code_rejected(self):
        self.orders_obj["code"]=10001
        self.write()
        with self.assertRaisesRegex(ValueError,"code 0"):
            normalize(self.orders,self.fills,self.out)
    def test_unknown_response_layout_rejected(self):
        self.orders_obj["data"]={"other":[]}
        self.write()
        with self.assertRaisesRegex(ValueError,"no list"):
            normalize(self.orders,self.fills,self.out)
    def test_duplicate_identity_rejected(self):
        self.orders_obj["data"]["orders"]*=2
        self.write()
        with self.assertRaisesRegex(ValueError,"duplicate"):
            normalize(self.orders,self.fills,self.out)
    def test_order_id_float_not_accepted(self):
        self.orders_obj["data"]["orders"][0]["orderID"]=1.2345e20
        self.write()
        with self.assertRaisesRegex(ValueError,"exact decimal"):
            normalize(self.orders,self.fills,self.out)
    def test_paged_response_list_can_be_joined(self):
        page=self.orders_obj
        self.orders_obj=[page]
        self.fills_obj=[self.fills_obj]
        self.write()
        self.assertEqual(normalize(self.orders,self.fills,self.out)["fills"],1)
    def test_csv_output_outside_input(self):
        self.write()
        with self.assertRaisesRegex(ValueError,"separate"):
            normalize(self.orders,self.fills,self.p)

if __name__=="__main__":
    unittest.main()