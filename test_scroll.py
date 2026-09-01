#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone pagination test, scrollbar-drag edition.

Drags the ScrollHandle of the building list in the open 星系:信息 window
step by step and reports which rows enter the UI tree per round.

Usage:  python test_scroll.py
"""
import sys
import time

DRAG_STEP_PX = 120  # drag the thumb down 120px per round

sys.path.insert(0, ".")
from eve_ui_bot import EveClient, extract_buildings, row_visible, run_cmd, DRAG_PS1, iter_nodes


def find_scrollbar(row):
    """(Scrollbar, ScrollHandle) nodes for the list this row belongs to"""
    n = row.parent
    while n is not None:
        if n.type == "Scroll":
            sb = handle = None
            for c in iter_nodes(n):
                if c.type == "Scrollbar":
                    sb = c
                if c.type == "ScrollHandle":
                    handle = c
            return sb, handle
        n = n.parent
    return None, None


def main():
    client = EveClient()
    client.refresh()
    print(f"EVE pid={client.pid}, origin={client.origin}")

    seen = set()
    idle_rounds = 0
    for rnd in range(1, 9):
        client.refresh()
        win = client.find_window(lambda w: "星系：信息" in w.texts())
        if win is None:
            sys.exit("system showinfo window not found.")
        rows = extract_buildings(win)
        if not rows:
            sys.exit("no building rows found (is the 建筑 tab active?)")
        visible = [(name, row) for name, row in rows if row_visible(row)]
        new = [name for name, _ in visible if name not in seen]
        seen.update(name for name, _ in visible)
        print(f"\n== round {rnd}: tree rows={len(rows)} visible={len(visible)} new={len(new)}")
        for name, row in visible:
            print(f"    y={row.y:5d} {name[:52]}")

        sb, handle = find_scrollbar(visible[0][1])
        if sb is None or handle is None:
            print("   no scrollbar (list fits viewport) - done")
            break
        sx = handle.x + handle.size()[0] // 2
        sy = handle.y + handle.size()[1] // 2
        track_bottom = sb.y + sb.size()[1]
        target_y = min(sy + DRAG_STEP_PX, track_bottom - handle.size()[1] // 2 - 2)
        print(f"   scrollbar track={sb.rect()} handle={handle.rect()} -> drag {sy} -> {target_y}")
        if target_y <= sy + 2:
            print("   thumb already at bottom - done")
            break
        run_cmd(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(DRAG_PS1),
                 "-x1", str(client.origin[0] + sx), "-y1", str(client.origin[1] + sy),
                 "-x2", str(client.origin[0] + sx), "-y2", str(client.origin[1] + target_y),
                 "-procId", str(client.pid)])
        time.sleep(1.2)

        if not new:
            idle_rounds += 1
            if idle_rounds >= 2:
                break
        else:
            idle_rounds = 0

    print(f"\ntotal distinct building rows seen across rounds: {len(seen)}")


if __name__ == "__main__":
    main()
