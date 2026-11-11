import json
import shutil
from pathlib import Path

before_dir = Path("evidence/v3-09/before")
before_dir.mkdir(parents=True, exist_ok=True)

modes = ["ALL_SIGNALS", "ONE_TRADE_PER_COMBO", "BINGX_PORTFOLIO"]
summary_lines = []

for m in modes:
    src_json = Path(f"runs/golden/v3-08/{m}/analysis.json")
    dst_json = before_dir / f"{m}_analysis_before.json"
    shutil.copyfile(src_json, dst_json)

    with open(src_json, "r") as f:
        data = json.load(f)
    combos = data.get("combos", [])
    combo_ids = [c.get("combo_id") for c in combos]
    combo_names = [f"{c.get('symbol')}/{c.get('combo_name')}" for c in combos]
    summary_lines.append(f"Mode: {m}")
    summary_lines.append(f"  Combos count: {len(combos)} (Expected from config: 20)")
    summary_lines.append(f"  Combo IDs present: {combo_ids}")
    summary_lines.append(f"  Keys present in old analyzer: {combo_names}")
    summary_lines.append("")

summary_text = "\n".join(summary_lines)
print(summary_text)
(before_dir / "combo_count_before.txt").write_text(summary_text, encoding="utf-8")
