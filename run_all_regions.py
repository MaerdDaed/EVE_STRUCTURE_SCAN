#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch runner: traverse every EVE region with eve_ui_bot, with checkpoint resume.

Reports live in output/<YYYY-MM-DD>/region-report-<名称>.json (see
report_store.py). On startup the runner offers the choice between:

  update - the newest existing report of each region (latest date directory,
           looking back at most report_store.LOOKBACK_DIRS directories for
           regions the latest day does not cover) is copied into today's
           directory and reconciled system by system: buildings added to or
           removed from the game are picked up, known ones are kept without
           re-opening their windows.

  fresh  - today's directory is emptied and every region is scanned from
           scratch. An interrupted fresh run is best resumed with update
           mode, which continues from today's partial files.

A region counts as DONE when today's directory holds its report with at least
one constellation and complete=true. Done regions are skipped, so a batch can
simply be re-launched after an interruption (daily downtime, client restart,
Ctrl+C) and it continues where it stopped.

Usage:
    python run_all_regions.py                  # ask update-vs-fresh, then run all pending regions
    python run_all_regions.py --update         # update existing data without asking
    python run_all_regions.py --fresh          # generate everything anew without asking
    python run_all_regions.py --dry-run        # show the plan without running
    python run_all_regions.py --save-snapshots # do write runs/ snapshot files
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eve_ui_bot
import report_store

TOOL_DIR = Path(__file__).resolve().parent

REGIONS = [
    "德里克", "伏尔戈", "静寂谷", "UUA-F4（特殊）", "底特里德", "邪恶湾流",
    "地窖", "灼热之径", "因斯姆尔", "特布特", "大荒野", "柯尔斯", "糟粕之域",
    "卡彻", "维纳尔", "长征", "J7HZ-F（特殊）", "螺旋之域", "A821-A（特殊）",
    "塔什蒙贡", "域外走廊", "混浊", "黑渊", "伊梅瑟亚", "琉蓝之穹", "摩登赫斯",
    "对舞之域", "西玛特尔", "绝径", "金纳泽", "赛塔德洱", "卡勒瓦拉阔地",
    "德克廉", "破碎", "埃维希尔", "幽暗之域", "埃索特亚", "欧莎", "辛迪加",
    "美特伯里斯", "多美", "孤独之域", "特纳", "斐德", "普罗维登斯", "宁静之域",
    "卡尼迪", "逑瑞斯", "云环", "卡多尔", "钴蓝边域", "艾里迪亚", "血脉",
    "非塔波利斯", "外环", "源泉之域", "摄魂之域", "绝地之域", "特里菲斯",
    "欧米斯特", "贝斯", "精华之域", "柯埃佐", "佩利根弗", "吉勒西斯",
    "维格温铎", "暗涌之域", "波赫文",
]


def report_path(name):
    return report_store.today_dir() / report_store.report_filename(name)


def is_done(name):
    """a region is complete when today's directory holds its report, it lists
    constellations and it was fully walked (reports written by interrupted
    runs carry complete=False and are re-run in merge mode to fill the gaps)"""
    p = report_path(name)
    if not p.exists():
        return False
    return report_store.is_complete(report_store.load_json(p))


def baseline_sources(names):
    """where each region's update baseline would come from: {name: date_dir or None}"""
    sources = {}
    for name in names:
        p, _ = report_store.find_report(name)
        sources[name] = p.parent.name if p is not None else None
    return sources


def choose_mode(argv):
    """--update/--fresh win; otherwise report_store decides (asks on a
    terminal, defaults to update elsewhere)"""
    if "--fresh" in argv:
        return "fresh"
    if "--update" in argv:
        return "update"
    return report_store.prompt_mode()


def main(argv):
    dry_run = "--dry-run" in argv
    if "--save-snapshots" in argv:
        eve_ui_bot.SAVE_SNAPSHOTS = True

    report_store.migrate_legacy_reports()
    mode = choose_mode(argv)
    print(f"启动模式: {'全新生成' if mode == 'fresh' else '更新已有数据'}")

    if mode == "fresh" and not dry_run:
        removed = report_store.clear_today_reports()
        if removed:
            print(f"已清空今日目录 output/{report_store.today_dirname()}/ "
                  f"中的 {len(removed)} 个报告文件,将从头生成")

    candidates = [r for r in REGIONS if "特殊" not in r]
    if mode == "fresh" and dry_run:
        # preview of the fresh-mode clear: today's files would be removed first,
        # so nothing may count as "done" in the plan
        if report_store.today_dir().is_dir():
            n = len(list(report_store.today_dir().glob("region-report-*.json")))
            if n:
                print(f"(dry-run 预览: 实际运行会先清空今日目录 output/"
                      f"{report_store.today_dirname()}/ 的 {n} 个报告文件)")
        done, pending = [], candidates
    else:
        done = [r for r in candidates if is_done(r)]
        pending = [r for r in candidates if not is_done(r)]

    print(f"星域总数 {len(REGIONS)}: 特殊 {len(REGIONS) - len(candidates)} 已排除, "
          f"已完成 {len(done)} (断点跳过), 待执行 {len(pending)}")
    if done:
        print("已完成:", "、".join(done))
    if pending:
        print("待执行:", "、".join(pending))
    if pending and mode == "update":
        sources = baseline_sources(pending)
        from_today = [n for n, s in sources.items() if s == report_store.today_dirname()]
        from_older = sorted({s for n, s in sources.items()
                             if s is not None and s != report_store.today_dirname()})
        missing = [n for n, s in sources.items() if s is None]
        if from_older:
            print("缺项回溯: 以下日期目录将补齐最新日期缺失的星域: "
                  + "、".join(from_older)
                  + f" (共 {len(pending) - len(from_today) - len(missing)} 个星域)")
        if missing:
            print("无历史数据、将全新生成的星域: " + "、".join(missing))
    if dry_run:
        return

    failed = []
    for i, name in enumerate(pending, 1):
        print(flush=True)
        print(f"########## [{i}/{len(pending)}] 星域 {name} ##########", flush=True)
        started = time.time()
        try:
            eve_ui_bot.run(name, mode=mode)
            status = "OK" if is_done(name) else "FAILED (report incomplete, will retry on next start)"
        except SystemExit as e:
            status = f"EXITED: {e}"
        except KeyboardInterrupt:
            print("interrupted by user - resume by rerunning this script")
            raise
        except Exception as e:
            status = f"ERROR: {e!r}"
        if not status.startswith("OK"):
            failed.append((name, status))
            eve_ui_bot.log(f"[batch] region {name} -> {status} "
                           f"({time.time() - started:.0f}s)")
        else:
            eve_ui_bot.log(f"[batch] region {name} -> OK ({time.time() - started:.0f}s)")

    print(flush=True)
    print("########## 批量结束 ##########")
    print(f"完成 {len(pending) - len(failed)}/{len(pending)}")
    if failed:
        print("失败星域(重新运行本脚本会自动重试它们):")
        for name, status in failed:
            print(f"  {name}: {status}")


if __name__ == "__main__":
    main(sys.argv)
