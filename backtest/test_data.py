import unittest,tempfile
from pathlib import Path
import pandas as pd
from bingx_data import download_history,parse_klines,validate
A=1782864000000;STEP=60000
class Fake:
    def __init__(self,mode='normal'):self.mode=mode;self.calls=0
    def klines(self,symbol,interval,start_ms,end_ms,limit):
        self.calls+=1
        ts=list(range(start_ms,end_ms,STEP))
        if self.mode=='cap':ts=ts[-100:]
        if self.mode=='gap':ts=[t for t in ts if t!=A+STEP]
        if self.mode=='duplicate':ts=ts+[ts[0]]
        return [dict(time=t,open='10',high='12',low='9',close='11',volume='3') for t in reversed(ts)]
class Tests(unittest.TestCase):
    def run_download(self,client,path=None):return download_history(client,'BTC-USDT','1m',pd.to_datetime(A,unit='ms',utc=True),pd.to_datetime(A+600*STEP,unit='ms',utc=True),path,sleep_s=0)
    def test_capped_reverse_pages(self):self.assertEqual(len(self.run_download(Fake('cap'))),600)
    def test_gap_fails(self):
        with self.assertRaises(ValueError):self.run_download(Fake('gap'))
    def test_duplicate_fails(self):
        with self.assertRaises(ValueError):self.run_download(Fake('duplicate'))
    def test_cache(self):
        with tempfile.TemporaryDirectory() as p:
            f=Fake();path=Path(p)/'data.pkl';self.run_download(f,path);calls=f.calls;self.run_download(f,path);self.assertEqual(f.calls,calls)
    def test_invalid_ohlc(self):
        d=parse_klines([dict(time=A,open=10,close=11,high=8,low=9,volume=1)])
        self.assertFalse(validate(d,'1m',pd.to_datetime(A,unit='ms',utc=True),pd.to_datetime(A+STEP,unit='ms',utc=True))['valid'])
    def test_partial_future(self):
        with self.assertRaises(ValueError):download_history(Fake(),'BTC-USDT','1m','2099-01-01','2099-01-02',sleep_s=0)
if __name__=='__main__':unittest.main()
