from __future__ import annotations

L2_VARIANTS=[
    "OFF",
    "OB",
    "SMC_VETO",
    "SMC_STRICT",
    "SMC_VETO_OB",
    "SMC_STRICT_OB",
]

def approved(side,smcdir,obblocked,variant):
    edir=1 if side=="L" else -1
    if "STRICT" in variant:
        smc_ok=(smcdir==edir)
    elif "VETO" in variant:
        smc_ok=(smcdir!=-edir)
    else:
        smc_ok=True
    ob_ok=(not obblocked) if ("OB" in variant) else True
    return smc_ok and ob_ok
