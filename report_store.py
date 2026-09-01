#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dated storage for the region reports: output/<YYYY-MM-DD>/region-report-<名称>.json

Rules shared by eve_ui_bot.py and run_all_regions.py:

- every report is written into a per-day directory below output/;
- on startup the newest date directory is the loading candidate;
- when a region has no report in the newest directory (not a full pass yet),
  the lookup walks back over at most LOOKBACK_DIRS older directories to find
  the most recent generated data for it;
- "update" mode copies a historical report into today's directory first and
  then updates it in place - one region at a time as it is processed, never a
  bulk copy of everything, so a file in today's directory has always been
  picked up by the update pass (or is today's own output);
- "fresh" mode starts today's directory from scratch: existing region reports
  of today are removed up front (after the user confirmed the choice);
  an interrupted fresh run can be resumed via update mode, where today's
  partial files are the baseline.
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = TOOL_DIR / "output"

LOOKBACK_DIRS = 2  # date directories below the newest one consulted for data

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _info(msg):
    print(msg, flush=True)


def report_filename(region_name):
    return f"region-report-{region_name}.json"


def today_dirname():
    return time.strftime("%Y-%m-%d")


def today_dir():
    return OUTPUT_DIR / today_dirname()


def date_dirs():
    """all date directories below output/, oldest first"""
    if not OUTPUT_DIR.is_dir():
        return []
    return sorted((d for d in OUTPUT_DIR.iterdir()
                   if d.is_dir() and _DATE_RE.match(d.name)), key=lambda d: d.name)


def baseline_dirs():
    """date directories consulted for existing data, newest first: the newest
    one plus at most LOOKBACK_DIRS further back"""
    return list(reversed(date_dirs()))[:1 + LOOKBACK_DIRS]


def load_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def is_complete(data):
    """a report counts as complete when it lists constellations and was fully
    walked (reports from interrupted runs carry complete=False)"""
    return bool(data) and data.get("星座数", 0) > 0 and data.get("complete", True)


def migrate_legacy_reports():
    """one-time move of the pre-dating flat reports (tool root) into date
    directories grouped by each file's modification date. Only runs while
    output/ contains no date directory at all."""
    if date_dirs():
        return []
    legacy = sorted(TOOL_DIR.glob("region-report-*.json"))
    moved = []
    for p in legacy:
        day = time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime))
        dest_dir = OUTPUT_DIR / day
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest_dir / p.name))
            moved.append((p.name, day))
        except OSError as e:
            _info(f"WARNING: could not move {p.name} into output/{day}: {e}")
    if moved:
        _info(f"已把 {len(moved)} 个旧报告文件从工具目录移入 output/ 的日期目录:")
        for name, day in moved:
            _info(f"  {name} -> output/{day}/")
    return moved


def find_report(region_name):
    """newest existing report of a region: newest date directory first, at
    most LOOKBACK_DIRS directories back. Returns (path, data) or (None, None)."""
    name = report_filename(region_name)
    for d in baseline_dirs():
        p = d / name
        if p.exists():
            data = load_json(p)
            if data is not None:
                return p, data
    return None, None


def materialize_baseline(region_name):
    """baseline report for an update run, always materialized in today's
    directory: the newest report found (looking back at most LOOKBACK_DIRS
    date directories) is copied there first when it lives in an earlier day,
    and the update then rewrites that copy in place. Per-region on purpose:
    the copy happens when the region is processed, so an interrupted run
    never leaves a pile of copied-but-never-updated files.
    Returns (path_in_today, data) or (None, None)."""
    src, _ = find_report(region_name)
    if src is None:
        return None, None
    dst = today_dir() / src.name
    if src.parent != dst.parent:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return dst, load_json(dst)


def clear_today_reports():
    """remove today's region reports (fresh mode) - never touches other days"""
    removed = []
    if today_dir().is_dir():
        for p in sorted(today_dir().glob("region-report-*.json")):
            p.unlink()
            removed.append(p.name)
    return removed


def coverage():
    """(day, path, report_count, complete_count) per consulted date directory,
    newest first; .backup.json files are not counted"""
    stats = []
    for d in baseline_dirs():
        files = [p for p in d.glob("region-report-*.json")
                 if not p.name.endswith(".backup.json")]
        n_complete = sum(1 for p in files if is_complete(load_json(p)))
        stats.append((d.name, d, len(files), n_complete))
    return stats


def prompt_mode():
    """ask on the terminal whether to update the existing data or to generate
    everything fresh. Without historical data (or on a non-interactive stdin)
    it decides by itself instead of asking."""
    stats = coverage()
    if not stats or all(n == 0 for _, _, n, _ in stats):
        _info(f"output/ 下没有已生成的星域报告,将全新生成到 output/{today_dirname()}/")
        return "fresh"
    _info("已找到历史数据 (缺项时最多往前的 "
          f"{LOOKBACK_DIRS} 个日期目录回找):")
    for day, _, n, n_complete in stats:
        _info(f"  output/{day}/: {n} 个星域报告 ({n_complete} 个完整)")
    _info(f"本次输出目录: output/{today_dirname()}/")
    if not sys.stdin.isatty():
        _info("非交互终端,默认选择: 更新已有数据")
        return "update"
    while True:
        try:
            choice = input(
                "请选择启动模式: [1] 更新已有数据 (复制历史报告到今日目录,逐星系核对建筑增删) "
                "[2] 全新生成 (清空今日目录后重扫全部) > ").strip()
        except EOFError:
            _info("标准输入不可读,默认选择: 更新已有数据")
            return "update"
        if choice in ("1", "", "1.", "更新", "update", "u"):
            return "update"
        if choice in ("2", "2.", "全新", "fresh", "f"):
            return "fresh"
        _info("请输入 1 或 2")
