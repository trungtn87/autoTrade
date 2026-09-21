"""Run only against the dedicated disposable PostgreSQL CI service."""
import concurrent.futures
import os
import uuid
from app.state import SignalState

url=os.environ['FINAL14_TEST_POSTGRES_URL']
state=SignalState('unused',url)
event=uuid.uuid4().hex;case='TEST|'+event
record={'case_id':case,'event_id':event,'symbol':'TEST'}
with concurrent.futures.ThreadPoolExecutor(4) as pool:
    result=list(pool.map(lambda _:SignalState('unused',url).claim_final14(record),range(8)))
assert sum(result)==1,result
assert not state.claim_final14({**record,'event_id':event+'2'})
state.update_final14(event,{'status':'accepted','entry_order_id':'123'})
restarted=SignalState('unused',url)
assert restarted.final14_records('TEST')[0]['entry_order_id']=='123'
restarted.update_final14(event,{'status':'closed'})
assert not restarted.claim_final14(record)
assert restarted.claim_final14({**record,'event_id':event+'2'})
# A stale response cannot reopen the old event while a new one owns the case.
restarted.update_final14(event,{'status':'active'})
assert len(restarted.final14_records('TEST'))==1
print('FINAL14 PostgreSQL atomic claim / restart / event dedupe PASS')
