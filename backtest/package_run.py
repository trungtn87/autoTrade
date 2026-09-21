from __future__ import annotations

import argparse, hashlib, json, os, platform, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha() -> str:
    try:
        return subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip()
    except Exception:
        return os.getenv("GITHUB_SHA","UNKNOWN")


def main():
    ap=argparse.ArgumentParser(description="Create immutable reproducible backtest run bundle")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--scope", required=True, help="e.g. C1, C1_GRID, PORTFOLIO")
    ap.add_argument("--result-dir", required=True)
    ap.add_argument("--data15", required=True)
    ap.add_argument("--config", required=True, help="JSON config used for this run")
    ap.add_argument("--standard-version", default="1.0")
    ap.add_argument("--out-dir", default="run_bundles")
    args=ap.parse_args()

    result_dir=Path(args.result_dir)
    data15=Path(args.data15)
    config_path=Path(args.config)
    if not result_dir.exists(): raise FileNotFoundError(result_dir)
    if not data15.exists(): raise FileNotFoundError(data15)
    if not config_path.exists(): raise FileNotFoundError(config_path)

    now=datetime.now(timezone.utc).replace(microsecond=0)
    sha=git_sha()
    run_id=f"{now.strftime('%Y%m%dT%H%M%SZ')}__{args.symbol}__{args.scope}__{sha[:8]}"
    out_root=Path(args.out_dir); out_root.mkdir(parents=True,exist_ok=True)
    bundle=out_root/run_id
    if bundle.exists(): raise FileExistsError(bundle)
    bundle.mkdir()

    shutil.copy2(config_path,bundle/"run_config.json")

    results_dst=bundle/"results"
    shutil.copytree(result_dir,results_dst)

    data_obj=pd.read_pickle(data15)
    if not isinstance(data_obj.index,pd.DatetimeIndex):
        if "open_time" not in data_obj: raise ValueError("15m dataset lacks DatetimeIndex/open_time")
        data_obj.index=pd.to_datetime(data_obj["open_time"],unit="ms",utc=True)
    if data_obj.index.tz is None: data_obj.index=data_obj.index.tz_localize("UTC")
    else: data_obj.index=data_obj.index.tz_convert("UTC")

    data_meta={
        "path":str(data15),
        "sha256":sha256(data15),
        "rows":int(len(data_obj)),
        "first":None if data_obj.empty else str(data_obj.index.min()),
        "last":None if data_obj.empty else str(data_obj.index.max()),
    }
    (bundle/"data_checksums.json").write_text(json.dumps({"15m":data_meta},indent=2),encoding="utf-8")

    env={
        "python":sys.version,
        "platform":platform.platform(),
        "pandas":pd.__version__,
    }
    try:
        env["pip_freeze"]=subprocess.check_output([sys.executable,"-m","pip","freeze"],text=True).splitlines()
    except Exception:
        env["pip_freeze"]=[]
    (bundle/"environment.json").write_text(json.dumps(env,indent=2),encoding="utf-8")

    copied_files=[]
    for p in sorted(bundle.rglob("*")):
        if p.is_file() and p.name!="run_manifest.json":
            copied_files.append({
                "path":str(p.relative_to(bundle)),
                "sha256":sha256(p),
                "bytes":p.stat().st_size,
            })

    manifest={
        "run_id":run_id,
        "created_at_utc":now.isoformat(),
        "standard_version":args.standard_version,
        "symbol":args.symbol,
        "scope":args.scope,
        "git_sha":sha,
        "data_15m":data_meta,
        "config_file":"run_config.json",
        "result_dir":"results",
        "files":copied_files,
        "storage_status":{
            "github_code_versioned":True,
            "google_drive_uploaded":False,
            "google_drive_folder":None,
        },
    }
    (bundle/"run_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")

    zip_path=shutil.make_archive(str(bundle),"zip",root_dir=bundle.parent,base_dir=bundle.name)
    print(json.dumps({"run_id":run_id,"bundle_dir":str(bundle),"zip":zip_path,"data_sha256":data_meta["sha256"]},indent=2))


if __name__=="__main__":
    main()
