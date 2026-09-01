#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot repair: clear building lists that were cross-contaminated by the
wrong-window bug (a late-opening showinfo window of system A was read as
system B). Contaminated systems are emptied and flagged complete=False, so the
next merge run re-extracts them from the client.

A building is foreign when its name does not start with the owning system's
name (allowing the "系统 » 目标 - 桥" / "系统 8 - Moon 1" naming styles).
A system counts as contaminated when more than half of its buildings (and at
least 3) are foreign - player-named structures only ever trip 1-2 at a time.
"""
import json
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent


def is_foreign(name, sysname):
    if not name.startswith(sysname):
        return True
    rest = name[len(sysname):]
    return len(rest) > 0 and rest[0].isalnum()


def report_files():
    """every report in the dated layout output/<YYYY-MM-DD>/, plus any legacy
    flat ones left in the tool root"""
    pattern = "*/region-report-*.json"
    return sorted(TOOL_DIR.glob(f"output/{pattern}")) + sorted(TOOL_DIR.glob(pattern))


def main():
    for f in report_files():
        if f.name.endswith(".backup.json"):
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        changed = False
        for c in data.get("星座", []):
            for s in c.get("星系", []):
                sysname = s.get("名称", "")
                buildings = s.get("建筑", [])
                foreign = [b["名称"] for b in buildings
                           if is_foreign(b.get("名称", ""), sysname)]
                if len(foreign) >= 3 and len(foreign) * 2 > len(buildings):
                    print(f"{f}: 清空被污染星系 {sysname} 的 {len(buildings)} 条建筑 "
                          f"(异星系 {len(foreign)} 条, 如 {foreign[:2]})")
                    s["建筑"] = []
                    s["建筑数"] = 0
                    s["complete"] = False
                    changed = True
        if changed:
            data["complete"] = False
            with open(f, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            print(f"  -> {f} 已标记 complete=False, 将由合并模式重新提取\n")
    print("repair finished")


if __name__ == "__main__":
    main()
