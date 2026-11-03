"""Bounded, pure capture of facts ALREADY read by execution. Never does I/O."""
FIELDS = ('orderId', 'orderID', 'symbol', 'side', 'positionSide', 'positionId',
          'positionID', 'clientOrderId', 'clientOrderID', 'status', 'executedQty', 'origQty',
          'quantity', 'updateTime', 'filledTime', 'fillTime', 'triggerOrderId',
          'triggerOrderID', 'type')
MAX_ENTRY_OBSERVATIONS = 32


def order_evidence(row):
    """Capture only bounded scalar order facts, never headers/secrets or money."""
    try:
        return {key:value for key in FIELDS if key in row
                and isinstance((value := row[key]), (str, int, float))
                and not isinstance(value, bool) and len(str(value)) <= 256}
    except Exception:
        return {}


def capture_entry(state, row, query_client_order_id):
    """Piggyback on existing state persistence. Failure cannot affect protection."""
    try:
        observation = {'query_client_order_id':query_client_order_id,
                       'order':order_evidence(row)}
        if not observation['order']:
            return
        records = state.setdefault('analytics_entry_evidence', [])
        if observation not in records:
            if len(records) >= MAX_ENTRY_OBSERVATIONS:
                state['analytics_entry_evidence_truncated'] = True
            else:
                records.append(observation)
    except Exception:
        # Capture is optional metadata on the existing execution write. It must
        # not turn malformed legacy state into a failed order/protection call.
        return
