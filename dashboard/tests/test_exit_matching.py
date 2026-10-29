"""Broker exit type classification without BingX API calls."""
from types import SimpleNamespace as O

from autotrade_live.exits import extract_exit_spec, identify_exit, build_exit_events
from autotrade_live.mobile import dashboard_payload


def state(combo, entry, sl, tp, trail, signal_time=1792972799999):
    return {
        "order_id":entry, "entry_filled":True, "executed_qty":0.0012,
        "position_id":"shared-pid",
        "intent":{"combo":combo,"symbol":"BTC-USDT","side":"SELL",
                  "close_time":signal_time,"tp":78515.84,"sl":80652.32},
        "protection":{"sl":{"order_id":sl},"tp":{"order_id":tp},
                      "trailing":{"order_id":trail}},
        "trailing_activation_price":79692.93,"trailing_callback_rate":0.005,
    }


def order(oid,kind,created,trigger,qty=0.0006):
    raw={"positionId":"shared-pid", "priceRate":0.005}
    raw["activationPrice" if kind=="TRAILING_STOP_MARKET" else "stopPrice"] = trigger
    return O(order_id=oid,type=kind,created_ms=created,quantity=qty,
             symbol="BTC-USDT",side="BUY",position_side="SHORT",raw=raw)


def fill(oid,trade,qty,price,pnl,fee,when):
    return O(order_id=oid,trade_id=trade,qty=qty,price=price,
             realized_pnl=pnl,fee=fee,timestamp_ms=when,symbol="BTC-USDT",
             position_side="SHORT",side="BUY")


def test_confirmed_trailing_from_morning_c14_bingx_realized():
    spec=extract_exit_spec(state(104,"2114507408344829952",
        "2114507429634478080","2114507456343805952","2114507472525430784"))
    assert spec["combo"]=="C14"
    assert spec["exit_ids"]["SL"]=="2114507429634478080"
    trailing=order("2114507472525430784","TRAILING_STOP_MARKET",
                   spec["signal_time_ms"]+30000,79692.93)
    rows=[fill(trailing.order_id,"f1",0.0004,79756.1,0.0112,-0.01,
               spec["signal_time_ms"]+3600000),
          fill(trailing.order_id,"f2",0.0002,79756.1,0.0056,-0.004,
               spec["signal_time_ms"]+3600001)]
    events=build_exit_events([trailing],rows,[spec])
    assert len(events)==1
    e=events[0]
    assert (e["combo"],e["type"],e["confidence"])==("C14","TRAILING","ORDER_ID")
    assert abs(e["bingx_realized_pnl"]-0.0168)<1e-8
    assert abs(e["exit_fees"]+0.014)<1e-8
    assert e["qty"]==0.0006


def test_old_cancelled_sl_not_counted_as_an_exit_without_fill():
    spec=extract_exit_spec(state(104,"111","222","333","444"))
    stop=order("222","STOP_MARKET",spec["signal_time_ms"]+1000,80652.32)
    assert build_exit_events([stop],[],[spec])==[]


def test_price_time_only_candidate_is_visibly_uncertain():
    spec=extract_exit_spec(state(104,"111","222","333","444"))
    replacement=order("new555","STOP_MARKET",
                      spec["signal_time_ms"]+100000,80652.32)
    matched,method,kind=identify_exit(replacement.order_id,replacement,[spec])
    assert matched["combo"]=="C14"
    assert (method,kind)==("PRICE_TIME_CANDIDATE","SL")


def test_same_price_two_combos_must_not_assign_or_sum_as_known_pnl():
    s1=extract_exit_spec(state(104,"111","222","333","444"))
    s2=extract_exit_spec(state(7,"555","666","777","888"))
    stop=order("999","STOP_MARKET",s1["signal_time_ms"]+90000,80652.32)
    spec,method,kind=identify_exit(stop.order_id,stop,[s1,s2])
    assert spec is None and (method,kind)==("AMBIGUOUS","SL")
    result=build_exit_events([stop],[fill("999","f3",0.0006,80660,-1,-0.01,
                                        s1["signal_time_ms"]+100000)],[s1,s2])
    assert result[0]["combo"] is None
    assert result[0]["confidence"]=="AMBIGUOUS"


def test_order_id_cannot_override_conflicting_side_or_symbol():
    spec=extract_exit_spec(state(104,"111","222","333","444"))
    bad=order("222","STOP_MARKET",spec["signal_time_ms"]+1000,80652.32)
    bad.symbol="ETH-USDT"
    assert identify_exit("222",bad,[spec])[1]=="CONFLICT"


def test_multi_combo_shared_position_maps_only_the_right_exit_order():
    first=extract_exit_spec(state(104,"111","222","333","444"))
    second=extract_exit_spec(state(7,"555","666","777","888"))
    one=order("333","TAKE_PROFIT_MARKET",first["signal_time_ms"]+20000,78515.84)
    two=order("888","TRAILING_STOP_MARKET",second["signal_time_ms"]+25000,79692.93)
    events=build_exit_events([one,two],[
        fill("333","a",0.0006,78510,1.5,-0.02,first["signal_time_ms"]+30000),
        fill("888","b",0.0006,79690,0.6,-0.01,second["signal_time_ms"]+35000),
    ],[first,second])
    assert {(e["combo"],e["type"]) for e in events}=={("C14","TP"),("C7","TRAILING")}


def test_dashboard_exposes_exit_even_when_position_ledger_is_mixed():
    spec=extract_exit_spec(state(104,"111","222","333","444"))
    when=spec["signal_time_ms"]+30000
    records={
        "orders":[order("333","TAKE_PROFIT_MARKET",when-20000,78515.84)],
        "fills":[fill("333","f1",0.0006,78510,1.5,-0.02,when)],
        "links":[],"forces":[],"incomes":[],"runs":[],"positions":[],
        "exit_specs":[spec],
    }
    result=dashboard_payload(records)
    assert result["exit_events"][0]["combo"]=="C14"
    assert result["exit_events"][0]["type"]=="TP"
    assert result["exit_summary"]["matched_by_id"]==1
    assert result["exit_summary"]["bingx_exit_realized_usdt"]==1.5
    assert result["exit_summary"]["bingx_exit_fees_usdt"]==-0.02


def test_bingx_trigger_order_id_is_exact_even_for_large_json_integer():
    s1=extract_exit_spec(state(104,"111","222","333","444"))
    s2=extract_exit_spec(state(7,"555","666","777","888"))
    # The exchange creates a separate execution order; its triggerOrderId
    # points to the ORIGINAL conditional protection order.
    child=order("999","TRAILING_STOP_MARKET",s1["signal_time_ms"]+7200000,0)
    child.raw={"positionId":"shared-pid","triggerOrderId":444}
    matched,method,kind=identify_exit(child.order_id,child,[s1,s2])
    assert (matched["combo"],method,kind)==("C14","TRIGGER_ORDER_ID","TRAILING")


def test_trigger_id_prevents_wrong_price_time_attribution():
    s1=extract_exit_spec(state(104,"111","222","333","444"))
    child=order("999","STOP_MARKET",s1["signal_time_ms"]+10000,80652.32)
    child.raw["triggerOrderId"]="1234567"
    spec,confidence,kind=identify_exit(child.order_id,child,[s1])
    assert (spec,confidence,kind)==(None,"UNMAPPED","SL")


def test_trigger_id_conflicts_with_wrong_kind_and_exact_id():
    s1=extract_exit_spec(state(104,"111","222","333","444"))
    s2=extract_exit_spec(state(7,"555","666","777","888"))
    child=order("999","STOP_MARKET",s1["signal_time_ms"]+10000,80652.32)
    child.raw["triggerOrderId"]="444"
    assert identify_exit("999",child,[s1])[1]=="CONFLICT"
    child.raw["triggerOrderId"]="666"
    assert identify_exit("222",child,[s1,s2])[1]=="CONFLICT"


def test_trigger_mapping_counts_as_confirmed_bingx_exit_and_combo():
    spec=extract_exit_spec(state(104,"111","222","333","444"))
    child=order("999","TRAILING_STOP_MARKET",spec["signal_time_ms"]+3000,0)
    child.raw={"positionId":"shared-pid","triggerOrderId":"444"}
    when=spec["signal_time_ms"]+20000
    records={"orders":[child],
             "fills":[fill("999","f1",.0006,79000,.15,-.02,when)],
             "links":[],"forces":[],"incomes":[],"runs":[],"positions":[],
             "exit_specs":[spec]}
    result=dashboard_payload(records)
    assert result["exit_summary"]["matched_by_id"]==1
    assert result["exit_summary"]["matched_by_trigger"]==1
    assert result["combo_exits"][0]["combo"]=="C14"
    assert result["combo_exits"][0]["TRAILING"]==1
    assert result["combo_exits"][0]["realized_pnl"]==.15


def test_dashboard_shows_unverified_legacy_stop_even_if_cleanup_marked_done():
    spec=extract_exit_spec({
        **state(104,"111","222","333","444"),
        "exit_cleanup":{"status":"done","warnings":["LEGACY_SL_REPLACEMENT_UNVERIFIED"]},
        "lifecycle":{"pending_sl_replace":{"old_order_id":"222","client_order_id":"legacy"}},
    })
    result=dashboard_payload({"orders":[],"fills":[],"links":[],"forces":[],
                              "incomes":[],"runs":[],"positions":[],"exit_specs":[spec]})
    assert result["cleanup_risks"] == [
        {"combo":"C14","symbol":"BTC-USDT",
         "reason":"LEGACY_SL_REPLACEMENT_UNVERIFIED","cleanup_status":"done"}
    ]


def test_legacy_replaced_sl_exact_client_order_maps_c14_not_c7():
    # Real BingX 26/10 scenario: child triggerId was not saved, but the
    # executed child retained deterministic clientOrderId from the bot journal.
    c14=state(104,"2114507408344829952",
              "2114507429634478080","2114507456343805952",
              "2114507472525430784")
    c14["position_id"]="2114507408374190082"
    c14["lifecycle"]={"pending_sl_replace":{
        "old_order_id":"2114507429634478080",
        "client_order_id":"atv2sl-59e17321903490ce9181c5ddd1",
        "quantity":"0.0006"}}
    c7=state(7,"2114533815368241152","2114533834497822720",
             "2114533858506018816","2114533872947007488")
    c7["position_id"]="2114507408374190082"
    spec14=extract_exit_spec(c14)
    spec7=extract_exit_spec(c7)
    child=order("2114739087663648768","STOP_MARKET",
                1793028063000,80652.3,qty=.0006)
    child.raw={"positionID":"2114507408374190082",
               "triggerOrderId":"2114536413692792832",
               "stopPrice":"80652.3",
               "clientOrderId":"atv2sl-59e17321903490ce9181c5ddd1"}
    spec,confidence,kind=identify_exit(child.order_id,child,[spec14,spec7])
    assert (spec["combo"],confidence,kind)==("C14","LEGACY_SL_CLIENT_ID","SL")
    records={"orders":[child],"fills":[fill(child.order_id,"historical-fill",.0006,
              80655,-.57108,-.02,1793028063000)],"links":[],"forces":[],
              "incomes":[],"runs":[],"positions":[],"exit_specs":[spec14,spec7]}
    data=dashboard_payload(records)
    assert data["exit_summary"]["matched_by_id"]==1
    assert data["exit_summary"]["matched_by_legacy_client"]==1
    assert data["combo_exits"][0]["combo"]=="C14"
    assert data["combo_exits"][0]["SL"]==1
    assert data["cleanup_risks"]==[{"combo":"C14","symbol":"BTC-USDT",
        "reason":"LEGACY_SL_REPLACEMENT_UNVERIFIED","cleanup_status":"not_checked"}]


def test_legacy_sl_client_id_conflicting_position_qty_or_price_fail_closed():
    payload=state(104,"111","222","333","444")
    payload["position_id"]="9001"
    payload["lifecycle"]={"pending_sl_replace":{
        "old_order_id":"222","client_order_id":"atv2sl-0123456789abcdef",
        "quantity":.0006}}
    spec=extract_exit_spec(payload)
    for bad in ("position","quantity","price"):
        child=order("999","STOP_MARKET",spec["signal_time_ms"]+20000,80652.32)
        child.raw={"positionID":"9001","stopPrice":"80652.32",
                   "clientOrderId":"atv2sl-0123456789abcdef",
                   "triggerOrderId":"991"}
        if bad=="position": child.raw["positionID"]="9002"
        if bad=="quantity": child.quantity=.001
        if bad=="price": child.raw["stopPrice"]="70000"
        assert identify_exit(child.order_id,child,[spec])[1]=="CONFLICT"


def test_sw_versioned_js_and_css_no_long_lived_cache_first():
    from pathlib import Path
    folder=Path(__file__).parents[1]/"autotrade_live"/"static"
    html=(folder/"index.html").read_text()
    sw=(folder/"sw.js").read_text()
    import re
    js_ver=re.search(r'app.js\\?v=([a-zA-Z0-9-]+)',html)
    css_ver=re.search(r'app.css\\?v=([a-zA-Z0-9-]+)',html)
    assert js_ver and css_ver and js_ver.group(1)==css_ver.group(1)
    assert "const VERSION = '"+js_ver.group(1)+"'" in sw
    assert "autotrade-pwa-shell-v8" in sw
    assert "fetch(event.request, {cache:'no-store'})" in sw
    assert 'self.skipWaiting()' in sw
    assert 'self.clients.claim()' in sw


def test_confirmed_exit_performance_matches_trading_report_metrics():
    # One win and one loss, based only on the exact BingX exit order ID.
    a=extract_exit_spec(state(104,"111","222","333","444"))
    b=extract_exit_spec(state(7,"555","666","777","888"))
    first=order("222","STOP_MARKET",a["signal_time_ms"]+1000,80652.32)
    second=order("888","TRAILING_STOP_MARKET",b["signal_time_ms"]+1200,79692.93)
    f1=fill("222","a",.0006,80652,-2.0,-.10,a["signal_time_ms"]+5000)
    f2=fill("888","b",.0006,79692,1.0,-.10,b["signal_time_ms"]+7000)
    result=dashboard_payload({"orders":[first,second],"fills":[f1,f2],
        "links":[],"forces":[],"incomes":[],"runs":[],"positions":[],
        "exit_specs":[a,b]})
    performance=result["exit_performance"]
    assert performance["count"]==2
    assert performance["wins"]==1
    assert performance["losses"]==1
    assert performance["win_rate"]==50.0
    assert performance["pnl_usdt"]==-1.2
    assert performance["profit_factor"]==0.43  # 0.90 / 2.10
    assert len(performance["curve"])==2
    assert round(performance["curve"][-1]["v"],6)==-1.2
    # No invented position statistics from exits without mapped entries.
    assert result["summary"]["closed"]==0
    assert len(result["combo_exits"])==2
    assert {x["combo"] for x in result["combo_exits"]}=={"C7","C14"}
    assert result["filters"]["combos"]==["C14","C7"]


def test_confirmed_exit_summary_excludes_unmapped_and_price_time_candidates():
    spec=extract_exit_spec(state(104,"111","222","333","444"))
    proven=order("222","STOP_MARKET",spec["signal_time_ms"]+3000,80652.32)
    unmapped=order("998","STOP_MARKET",spec["signal_time_ms"]+3000,80652.32)
    unmapped.raw["triggerOrderId"]="123456789"
    records={"orders":[proven,unmapped],
        "fills":[fill("222","a",.0006,80652,-.5,-.01,spec["signal_time_ms"]+5000),
                 fill("998","b",.0006,80652,10.0,-.01,spec["signal_time_ms"]+6000)],
        "links":[],"forces":[],"incomes":[],"runs":[],"positions":[],
        "exit_specs":[spec]}
    report=dashboard_payload(records)
    assert report["exit_performance"]["count"]==1
    assert report["exit_performance"]["pnl_usdt"]==-.51
    assert report["exit_performance"]["win_rate"]==0
    assert report["exit_performance"]["profit_factor"]==0
    assert report["exit_summary"]["unmapped_or_ambiguous"]==1
    assert report["exit_events"][0]["combo"] is None


def test_no_wins_or_losses_has_no_invented_profit_factor():
    r=dashboard_payload({"orders":[],"fills":[],"links":[],"forces":[],
        "incomes":[],"runs":[],"positions":[],"exit_specs":[]})
    assert r["exit_performance"]["count"]==0
    assert r["exit_performance"]["profit_factor"] is None
    assert r["exit_performance"]["win_rate"] is None


def test_mobile_report_has_compact_kpis_and_no_double_counting():
    from pathlib import Path
    folder=Path(__file__).parents[1]/"autotrade_live"/"static"
    html=(folder/"index.html").read_text()
    script=(folder/"app.js").read_text()
    for name in ('net','count','winrate','pf','equity-chart','combo-list',
                 'history-list','history-from','history-to','bingx-trade-list','exit-list'):
        assert 'id="'+name+'"' in html
    assert 'BingX &amp; dữ liệu đối soát' in html
    assert '<details' in html
    assert "renderCurve(theory.history || [])" in script
    assert "status === 'closed'" in script
    assert "data.exit_summary" in script
    assert "ROI biến động giá" in html
    assert 'id="history-more"' in html
    assert 'id="refresh"' not in html
