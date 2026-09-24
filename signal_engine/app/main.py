from fastapi import FastAPI

app = FastAPI(title="AutoTrade Legacy Disabled")


@app.on_event("startup")
def startup() -> None:
    print("LEGACY_DISABLED scheduler=false bingx=false trading=false")


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "mode": "legacy_disabled",
        "scheduler": False,
        "bingx": False,
        "trading": False,
    }


@app.get("/status")
def status() -> dict:
    return {
        "status": "disabled",
        "reason": "V2 is now the only active project",
    }
