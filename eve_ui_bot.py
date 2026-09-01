#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EVE Online UI-tree driven traversal bot.

Flow: global search -> open region showinfo -> 相关星座 tab -> for each constellation:
      open showinfo -> 相关星系 tab -> for each system: open showinfo -> 建筑 tab
      -> for each building: open showinfo -> record type/name/owner.

All data comes from the game client's own UI tree (read-memory-64-bit.exe);
mouse input is sent at coordinates computed from that tree.

Reports are stored as output/<YYYY-MM-DD>/region-report-<名称>.json (see
report_store.py): on startup the newest date directory is the loading
candidate and the lookup goes back at most report_store.LOOKBACK_DIRS further
directories for regions it does not cover.

Usage:
    python eve_ui_bot.py 伏尔戈                     # traverse a region; asks update-vs-fresh when a terminal is attached
    python eve_ui_bot.py 伏尔戈 --update            # merge against the newest existing report (copies it into today's directory)
    python eve_ui_bot.py 伏尔戈 --fresh             # ignore older data; only today's own partial file is resumed
    python eve_ui_bot.py 伏尔戈 --save-snapshots    # also write runs/ snapshot files
    python eve_ui_bot.py --cleanup                  # close leftover windows, clear the search box
    python eve_ui_bot.py --test                     # run offline extractors against saved snapshots
"""
import ctypes
import copy
import json
import re
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import report_store

TOOL_DIR = Path(__file__).resolve().parent
EXE = TOOL_DIR / "read-memory-64-bit.exe"
CLICK_PS1 = TOOL_DIR / "click.ps1"
SENDKEYS_PS1 = TOOL_DIR / "sendkeys.ps1"
SCROLL_PS1 = TOOL_DIR / "scroll.ps1"
DRAG_PS1 = TOOL_DIR / "drag.ps1"
TREE_FILE = TOOL_DIR / "ui-tree-bot.json"
ROOT_ADDR_FILE = TOOL_DIR / "ui-root-address.txt"
RUNS_DIR = TOOL_DIR / "runs"

SAVE_SNAPSHOTS = False  # enabled by the --save-snapshots command line flag

SETTLE_SECONDS = 0.2  # initial wait after a click before the first UI-change check


LOG_FILE = TOOL_DIR / "eve_ui_bot.log"


def log(msg):
    line = time.strftime("[%H:%M:%S] ") + msg
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ival(v):
    """unwrap {'int': ..., 'int_low32': n} representations from the JSON"""
    if isinstance(v, dict):
        return v.get("int_low32")
    return v


# ---------------------------------------------------------------- tree reading

def run_cmd(args, timeout=30, **kw):
    """subprocess.run that survives non-UTF8 console output (GBK on Chinese
    Windows) and cannot block forever: on timeout the call is logged and fails."""
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        log(f"WARNING: command timed out after {timeout}s: {args[0]} {args[-1] if args else ''}")
        proc = subprocess.CompletedProcess(args, returncode=124, stdout=b"", stderr=b"timeout")
    if isinstance(proc.stdout, bytes):
        proc.stdout = proc.stdout.decode("utf-8", errors="replace")
    if isinstance(proc.stderr, bytes):
        proc.stderr = proc.stderr.decode("utf-8", errors="replace")
    return proc


def find_eve_pid():
    out = run_cmd(["tasklist", "/FI", "IMAGENAME eq ExeFile.exe", "/FO", "CSV"]).stdout or ""
    for line in out.splitlines():
        if line.lower().startswith('"exefile.exe"'):
            return int(line.split('","')[1])
    sys.exit("EVE Online client (ExeFile.exe) is not running.")


def read_tree():
    """Read the UI tree, preferring a root address cached for THIS pid, else full scan."""
    pid = find_eve_pid()
    args = [str(EXE), "read-memory-eve-online", f"--pid={pid}", f"--output-file={TREE_FILE}"]
    use_cache = False
    if ROOT_ADDR_FILE.exists():
        cached = ROOT_ADDR_FILE.read_text().split()
        if len(cached) == 2 and cached[0] == str(pid):
            args.append(f"--root-address={cached[1]}")
            use_cache = True
    # remove the previous result first: a leftover file from an earlier session
    # must never masquerade as a fresh reading
    TREE_FILE.unlink(missing_ok=True)
    # timeouts: a cached-root read must answer within 10 seconds - anything
    # longer is a hung walk over garbage memory and gets killed and retried;
    # a full-memory scan (startup / stale cache) can take a few minutes
    attempts = 3 if use_cache else 1
    proc = tree = None
    for attempt in range(1, attempts + 1):
        t0 = time.time()
        proc = run_cmd(args, timeout=10 if use_cache else 900)
        took = time.time() - t0
        if took > 10:
            log(f"WARNING: UI tree read took {took:.1f}s - killed or slow, retrying")
        tree = load_tree()
        # node sanity cap: a legit UI tree has hundreds to low thousands of
        # nodes; a walk over freed/garbage memory can explode to millions
        ok = (tree is not None and 50 < count_nodes(tree) < 20000
              and "I saved memory reading" in (proc.stdout or ""))
        if ok:
            # remember the address of the largest tree for fast re-reads (bound to this pid)
            m = re.search(r"from address (0x[0-9A-Fa-f]+)", proc.stdout or "")
            if m:
                ROOT_ADDR_FILE.write_text(f"{pid} {m.group(1)}")
            return tree
        timed_out = "timeout" in (proc.stderr or "")
        garbage = tree is not None and count_nodes(tree) >= 20000
        if use_cache and attempt < attempts and (timed_out or garbage):
            reason = "read timed out" if timed_out else "implausibly large tree (garbage walk)"
            log(f"  {reason} (attempt {attempt}/{attempts}) - retrying in 2s")
            time.sleep(2.0)
            TREE_FILE.unlink(missing_ok=True)
            continue
        break
    if use_cache:
        log("cached UIRoot address is stale, falling back to a full memory scan ...")
        ROOT_ADDR_FILE.unlink(missing_ok=True)
        return read_tree()
    sys.exit(f"reading the UI tree failed:\n{proc.stdout}\n{proc.stderr}")


def load_tree(path=None):
    path = Path(path) if path else TREE_FILE
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def count_nodes(node):
    return 1 + sum(count_nodes(c) for c in node.get("children") or [] if c)


# ------------------------------------------------------------------ node model

class Node:
    __slots__ = ("raw", "parent", "x", "y", "depth")

    def __init__(self, raw, parent=None, x=0, y=0, depth=0):
        self.raw = raw
        self.parent = parent
        self.depth = depth
        d = raw.get("dictEntriesOfInterest", {})
        self.x = x + (ival(d.get("_displayX")) or 0)
        self.y = y + (ival(d.get("_displayY")) or 0)

    @property
    def type(self):
        return self.raw.get("pythonObjectTypeName") or ""

    @property
    def dict(self):
        return self.raw.get("dictEntriesOfInterest", {})

    @property
    def name(self):
        return self.dict.get("_name")

    def children(self):
        return [Node(c, self, self.x, self.y, self.depth + 1)
                for c in self.raw.get("children") or [] if c]

    def size(self):
        return ival(self.dict.get("_displayWidth")), ival(self.dict.get("_displayHeight"))

    def center_on_screen(self, ox, oy):
        w, h = self.size()
        w = w or 0
        h = h or 0
        return (ox + self.x + w // 2, oy + self.y + h // 2)

    def rect(self):
        w, h = self.size()
        return (self.x, self.y, w or 0, h or 0)

    def texts(self):
        out = []

        def rec(n):
            d = n.dict
            t = d.get("_text") or d.get("_setText")
            if t is not None:
                if isinstance(t, dict):
                    t = json.dumps(t.get("dictEntriesOfInterest", {}), ensure_ascii=False)
                s = re.sub(r"<[^>]+>", "", str(t)).replace("&lt;", "<").replace("&gt;", ">").strip()
                if s:
                    out.append(s)
            for c in n.children():
                rec(c)

        rec(self)
        return out


def build_root(tree):
    return Node(tree)


def iter_nodes(root):
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children())


# ------------------------------------------------------------- client / window

class EveClient:
    def __init__(self):
        self.pid = find_eve_pid()
        self.hwnd = self._find_hwnd()
        self.origin = self._client_origin()
        self.tree = None
        self.root = None

    def _find_hwnd(self):
        user32 = ctypes.windll.user32
        result = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPVOID)
        def cb(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self.pid and user32.IsWindowVisible(hwnd):
                result.append(hwnd)
            return True

        user32.EnumWindows(cb, None)
        if not result:
            sys.exit("EVE window not found.")
        return result[0]

    def _client_origin(self):
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        pt = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(pt))
        return pt.x, pt.y

    # -- reading

    def refresh(self, save_snapshot=None):
        self.tree = read_tree()
        self.root = build_root(self.tree)
        if save_snapshot and SAVE_SNAPSHOTS:
            RUNS_DIR.mkdir(exist_ok=True)
            with open(RUNS_DIR / save_snapshot, "w", encoding="utf-8") as f:
                json.dump(self.tree, f, ensure_ascii=False)
        return self

    # -- windows

    def windows(self):
        """all open windows: (z_index_within_layer, layer_name, Node)"""
        out = []
        for layer in self.root.children():
            lname = layer.name or layer.type
            for i, w in enumerate(layer.children()):
                if w.type in ("InfoWindow", "ListWindow", "QuickMessage") or "Window" in w.type:
                    out.append((i, lname, w))
        return out

    def topmost_window_containing(self, point):
        """earlier sibling index = higher z; search layers top-down"""
        for _, lname, w in self.windows():
            if lname not in ("l_main", "l_abovemain", "l_modal", "l_utilmenu"):
                continue
            x, y, wd, ht = w.rect()
            px, py = point
            if px is None or (x <= px <= x + wd and y <= py <= y + ht):
                if px is None:
                    continue
                return (lname, w)
        return None

    def find_window(self, predicate):
        for _, _, w in self.windows():
            if predicate(w):
                return w
        return None

    def find_all_windows(self, predicate):
        return [w for _, _, w in self.windows() if predicate(w)]

    def find_entity_window(self, type_keyword, entity_name, attempts=3, close_mismatch=True):
        """find the showinfo window of the expected entity.

        The window must carry `entity_name` inside its first few texts (the
        caption area), so a window opened by an earlier click for another
        entity can never be mistaken for this one; such strays are closed
        instead. Polls up to `attempts` rounds 1s apart for slow opens.
        The scan always terminates: without close_mismatch it checks every
        candidate once per attempt and gives up."""
        for attempt in range(attempts):
            if attempt:
                time.sleep(1.0)
                self.refresh()
            while True:
                matches = self.find_all_windows(lambda w: type_keyword in content_label(w))
                match = next((w for w in matches
                              if any(entity_name in t for t in w.texts()[:4])), None)
                if match is not None:
                    return match
                if not close_mismatch or not matches:
                    break  # cannot make progress this pass
                log(f"  closing a {type_keyword} window of a different entity")
                if not self.close_window(matches[0]):
                    break
                # the window stack changed: rescan
        return None

    # -- input

    def click_node(self, node, double=False):
        px, py = node.center_on_screen(*self.origin)
        self._ensure_clickable(node, (px, py))
        args = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(CLICK_PS1), "-x", str(px), "-y", str(py)]
        run_cmd(args)
        if double:
            time.sleep(0.15)
            run_cmd(args)
        log(f"clicked {node.type} '{node.name}' at screen ({px},{py})")

    def _ensure_clickable(self, node, point):
        """refuse to click if another window covers the target point; try to fix by closing covering showinfo windows"""
        target_win = self._window_of(node)
        for z, lname, w in self.windows():
            if w is target_win or lname not in ("l_main",):
                continue
            x, y, wd, ht = w.rect()
            if x <= point[0] <= x + wd and y <= point[1] <= y + ht:
                # is w above the target window? (smaller sibling index = higher z)
                if target_win is not None and self._z_of(w) < self._z_of(target_win):
                    log(f"target is covered by window '{content_label(w)}', closing it ...")
                    self.close_window(w)

    def _z_of(self, win_node):
        for i, _, w in self.windows():
            if w is win_node:
                return i
        return 9999

    def _window_of(self, node):
        n = node
        while n is not None:
            if n.type in ("InfoWindow", "ListWindow"):
                return n
            n = n.parent
        return None

    def send_keys(self, keys):
        run_cmd(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(SENDKEYS_PS1),
                 "-keys", keys, "-procId", str(self.pid)])

    def scroll_at(self, px, py, delta=-360):
        """mouse-wheel scroll at client coordinates (px, py); negative delta scrolls down.

        EVE only processes wheel input while focused, so the client window is
        brought to the foreground first."""
        run_cmd(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(SCROLL_PS1),
                 "-x", str(self.origin[0] + px), "-y", str(self.origin[1] + py),
                 "-delta", str(delta), "-procId", str(self.pid)])

    def drag(self, x1, y1, x2, y2):
        """left-button drag between client coordinates (EVE is foregrounded first)"""
        run_cmd(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(DRAG_PS1),
                 "-x1", str(self.origin[0] + x1), "-y1", str(self.origin[1] + y1),
                 "-x2", str(self.origin[0] + x2), "-y2", str(self.origin[1] + y2),
                 "-procId", str(self.pid)])

    # -- window management

    def close_window(self, win):
        for n in iter_nodes(win):
            if n.name == "CloseButtonIcon" and n.type == "ButtonIcon":
                label = content_label(win)
                changed = self.click_and_wait_change(n, purpose=f"closing '{label}'")
                return changed
        log(f"warning: no close button found on window '{content_label(win)}'")
        return False

    def click_and_wait_change(self, node, snapshot=None, purpose=""):
        """click, then poll until the UI actually changes.

        First check after SETTLE_SECONDS (0.2s); if nothing changed, retry twice
        at 1s intervals. Returns False (after logging an error) when the UI never
        changed, so the caller can move on to the next step."""
        before = ui_signature(self)
        self.click_node(node)
        time.sleep(SETTLE_SECONDS)
        self.refresh(save_snapshot=snapshot)
        for _ in range(2):
            if ui_signature(self) != before:
                return True
            time.sleep(1.0)
            self.refresh(save_snapshot=snapshot)
        if ui_signature(self) != before:
            return True
        log("ERROR: UI did not change after clicking "
            f"{node.type} '{node.name}'" + (f" ({purpose})" if purpose else "")
            + " - continuing with the next step")
        return False


def content_label(win):
    texts = win.texts()
    return texts[0] if texts else win.type


def ui_signature(client):
    """fingerprint of the open-window set + their content, used to detect UI changes"""
    sig = []
    for _, lname, w in client.windows():
        sig.append((lname, w.type, w.name, tuple(w.texts()[:10])))
    return hash(tuple(sig))


# ----------------------------------------------------------------- extraction

def strip_color_tags(s):
    return re.sub(r"<[^>]+>", "", s)


def parse_location_entries(win):
    """rows of a showinfo list (LocationTextEntry / LabelLocationTextTop / ...): [(sec_or_None, text, node)]"""
    out = []
    for n in iter_nodes(win):
        if "Location" in n.type and re.match(r"entry_\d+$", n.name or ""):
            raw = None
            for c in iter_nodes(n):
                t = c.dict.get("_setText") or c.dict.get("_text")
                if isinstance(t, str) and t.strip() and "showinfo:" not in t:
                    raw = t
                    break
            if raw is None:
                continue
            plain = strip_color_tags(raw.replace("<t>", " ")).strip()
            plain = re.sub(r"\s+", " ", plain)
            m = re.match(r"^(-?[\d\.]+) (.+)$", plain)
            if m:
                out.append((m.group(1), m.group(2).strip(), n))
            else:
                out.append((None, plain, n))
    return out


def section_header_positions(win, title):
    """y positions of section header texts like 建筑 / 空间站 / 路径"""
    out = []
    for n in iter_nodes(win):
        t = n.dict.get("_text") or n.dict.get("_setText")
        if isinstance(t, str) and t.strip() == title:
            out.append(n)
    return out


def extract_buildings(sys_win):
    """rows of the building list (visible after activating the 建筑 tab)"""
    return [(text, node) for sec, text, node in parse_location_entries(sys_win)]


def click_tab(client, win, title, snapshot=None):
    """activate a showinfo tab (相关星系 / 建筑 / ...) by clicking its label; returns False if absent"""
    for n in iter_nodes(win):
        if get_own_text(n) == title:
            client.click_and_wait_change(n, snapshot=snapshot, purpose=f"activating tab '{title}'")
            return True
    return False


def extract_systems(const_win):
    """entries of the 相关星系 section: [(sec, name, node)]"""
    return [(sec, text, node) for sec, text, node in parse_location_entries(const_win) if sec is not None]


def get_own_text(node):
    """the node's own text (not descendants'), with markup stripped"""
    t = node.dict.get("_text") or node.dict.get("_setText")
    if isinstance(t, dict):
        t = t.get("dictEntriesOfInterest", {}).get("_text")
    if not isinstance(t, str):
        return None
    return strip_color_tags(t.replace("<t>", " ")).strip()


def extract_building_details(win):
    """from a building showinfo window: (full_caption, name, type, owner)

    The header block has named nodes: TextBody 'caption' = "<name> (<owner>)",
    TextBody 'subCaption' = "<group> - <type>".
    """
    caption_text = None
    sub_text = None
    for n in iter_nodes(win):
        if caption_text is None and n.name == "caption":
            caption_text = get_own_text(n)
        if sub_text is None and n.name == "subCaption":
            sub_text = get_own_text(n)
    name, owner = caption_text, None
    m = re.search(r"^(.*?)\s*\((.+)\)$", caption_text or "")
    if m:
        name, owner = m.group(1).strip(), m.group(2).strip()
    return caption_text, name, sub_text, owner


# ----------------------------------------------------------------- input (UI)

def row_info_button(node):
    """the '!' showinfo button at the right end of the list row containing this node"""
    row = node
    while row is not None and not re.match(r"entry_\d+$", row.name or ""):
        row = row.parent
    if row is None:
        return None
    for n in iter_nodes(row):
        if n.type == "InfoIcon":
            return n
    return None


def open_row(client, node, snapshot=None):
    """open the showinfo of a list row: click its '!' button (falls back to the row label)"""
    btn = row_info_button(node)
    if btn is None:
        log("  no '!' button found on the row, clicking the label instead")
    return client.click_and_wait_change(btn if btn is not None else node, snapshot=snapshot)


def row_visible(row):
    """a row is clickable only if its '!' button lies inside the scroll viewport"""
    btn = row_info_button(row)
    target = btn if btn is not None else row
    w, h = target.size()
    cx = target.x + (w or 0) // 2
    cy = target.y + (h or 0) // 2
    clip = None
    n = target.parent
    while n is not None:
        if n.name == "__clipper":
            clip = n
            break
        n = n.parent
    if clip is None:
        return True
    cw, ch = clip.size()
    return clip.x <= cx <= clip.x + (cw or 0) and clip.y <= cy <= clip.y + (ch or 0)


SCROLLBAR_DRAG_STEP = 120  # px per scroll round (validated against a live 32-row list)


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


def scroll_list_down(client, rows):
    """drag the scrollbar thumb of the list containing `rows` down one step.

    Returns False when there is no scrollbar or the thumb is at the bottom."""
    sb, handle = find_scrollbar(rows[0][-1])
    if sb is None or handle is None:
        log(f"    [scroll] no scrollbar found on the list")
        return False
    sx = handle.x + handle.size()[0] // 2
    sy = handle.y + handle.size()[1] // 2
    track_bottom = sb.y + sb.size()[1]
    target_y = min(sy + SCROLLBAR_DRAG_STEP, track_bottom - handle.size()[1] // 2 - 2)
    log(f"    [scroll] track={sb.rect()} handle_y={handle.y} drag {sy} -> {target_y}")
    if target_y <= sy + 2:
        log("    [scroll] thumb already at bottom")
        return False
    client.drag(sx, sy, sx, target_y)
    return True


def iter_scroll_rows(client, win_pred, extract_fn, tag, key_index=0, stats=None):
    """incrementally walk a scrollable list, top to bottom.

    Yields the currently visible unprocessed rows ordered by y (top first);
    when every visible row has been processed, scrolls the list down to reveal
    more and continues. stats["complete"] is set to True when the end of the
    list was reached, False when the walk had to give up early.
    """
    processed = set()
    idle_scrolls = 0
    stuck_rounds = 0
    batch = 0
    while True:
        client.refresh()
        win = client.find_window(win_pred)
        if win is None:
            if stats is not None:
                stats["complete"] = False
            return
        items = extract_fn(win)
        visible = sorted(
            (it for it in items if it[key_index] not in processed and row_visible(it[-1])),
            key=lambda it: it[-1].y)
        if visible:
            idle_scrolls = 0
            log(f"  [{tag}] batch {batch}: {len(visible)} row(s) to process")
            for it in visible:
                processed.add(it[key_index])
                yield it
            batch += 1
            continue
        # everything visible is processed: drag the scrollbar down to reveal more
        idle_scrolls += 1
        anchor = visible or sorted((it for it in items), key=lambda it: it[-1].y)
        handle_y_before = None
        if anchor:
            _, handle_before = find_scrollbar(anchor[0][-1])
            handle_y_before = handle_before.y if handle_before else None
        if not anchor or not scroll_list_down(client, anchor):
            log(f"  [{tag}] list exhausted ({len(processed)} processed)")
            if stats is not None:
                stats["complete"] = True
            return
        log(f"  [{tag}] list bottom reached, dragging scrollbar down (round {idle_scrolls}) ...")
        time.sleep(1.2)
        client.refresh()
        # a drag that leaves the thumb in place counts as stuck; 3 in a row = give up
        win_now = client.find_window(win_pred)
        handle_y_after = None
        if win_now is not None:
            rows_now = extract_fn(win_now)
            if rows_now:
                _, handle_after = find_scrollbar(rows_now[0][-1])
                handle_y_after = handle_after.y if handle_after else None
        if handle_y_before is not None and handle_y_after == handle_y_before:
            stuck_rounds += 1
            log(f"    [scroll] WARNING: thumb did not move (handle_y still {handle_y_before})")
            if stuck_rounds >= 3:
                log(f"  [{tag}] scrollbar stopped responding - giving up ({len(processed)} processed)")
                if stats is not None:
                    stats["complete"] = False
                return
        else:
            stuck_rounds = 0


def find_search_input(client):
    for n in iter_nodes(client.root):
        if n.name == "MapViewSearchEdit":
            continue  # that is the star map search, not the global one
        if n.name == "searchEdit" or n.type in (
                "SearchInput", "SingleLineEdit", "SingleLineEditText", "TextInput", "EveEdit"):
            w, h = n.size()
            if (w or 0) > 80:  # skip tiny decoy nodes
                return n
    return None


CATEGORY_CAPTIONS = {"人物", "军团", "星域", "星座", "星系", "空间站", "建筑", "行星", "恒星", "星门"}


def row_label(row):
    """the display text of a list row (skipping category captions like 星座)"""
    for n in iter_nodes(row):
        t = get_own_text(n)
        if t and t not in CATEGORY_CAPTIONS:
            return t
    return None


def row_group_label(row):
    """text of a group header row, e.g. '星域 (1)'"""
    for n in iter_nodes(row):
        t = get_own_text(n)
        if t:
            return t
    return None


def extract_list_rows(win):
    """openable list rows (those carrying a '!' button): [(label, row_node)]"""
    out = []
    for n in iter_nodes(win):
        if re.match(r"entry_\d+$", n.name or "") and (n.size()[0] or 0) > 200:
            if row_info_button(n) is None:
                continue  # group header rows have no '!' button
            label = row_label(n)
            if label:
                out.append((label, n))
    return out


def pick_search_result(client, name, type_hint):
    """find the search-result row named `name`, preferring the group matching `type_hint` (星域/星座/...)"""
    best = None
    for _, _, w in client.windows():
        rows = [n for n in iter_nodes(w)
                if re.match(r"entry_\d+$", n.name or "") and (n.size()[0] or 0) > 200]
        rows.sort(key=lambda n: n.y)
        current_group = ""
        for n in rows:
            if row_info_button(n) is None:
                current_group = row_group_label(n) or current_group
                continue
            if row_label(n) == name:
                score = (0 if type_hint in current_group else 1, n.y)
                if best is None or score < best[0]:
                    best = (score, n)
    return best[1] if best else None


def expand_group_if_collapsed(client, type_hint):
    """if the search-results group `type_hint` (星域/星座/...) is collapsed, click it open.

    Returns True when an expander was clicked, False when the group is already
    expanded (or absent)."""
    for _, _, w in client.windows():
        rows = [n for n in iter_nodes(w)
                if re.match(r"entry_\d+$", n.name or "") and (n.size()[0] or 0) > 150]
        rows.sort(key=lambda n: n.y)
        header = None
        for n in rows:
            if row_info_button(n) is None and (row_group_label(n) or "").startswith(type_hint):
                header = n
                break
        if header is None:
            continue
        # data rows below this header, up to the next header row
        next_header_y = min((n.y for n in rows if n.y > header.y and row_info_button(n) is None),
                            default=10 ** 9)
        data_below = any(row_info_button(n) is not None and n.y < next_header_y
                         for n in rows if n.y > header.y)
        if data_below:
            return False  # already expanded
        expander = None
        for n in iter_nodes(header):
            if n.name == "expanderParent":
                expander = n
                break
        client.click_node(expander if expander is not None else header)
        return True
    return False


def search_entity(client, name, type_hint):
    """search the global search box and open the matching result's showinfo"""
    input_node = find_search_input(client)
    if input_node is None:
        sys.exit("search input not found in UI tree - open the global search window (放大镜图标) and rerun.")
    client.click_node(input_node)
    time.sleep(0.6)
    run_cmd(["powershell", "-Command", f"Set-Clipboard -Value '{name}'"])
    client.send_keys("^a^v{ENTER}")  # select-all, paste, enter
    log(f"searched for '{name}'")

    # results load asynchronously: poll, re-reading the tree, up to 5 attempts
    node = None
    for attempt in range(1, 6):
        time.sleep(1.0)
        client.refresh()
        node = pick_search_result(client, name, type_hint)
        if node is not None:
            log(f"  search result appeared after {attempt} attempt(s)")
            break
        # the matching group may be collapsed: expand it and retry
        if expand_group_if_collapsed(client, type_hint):
            log(f"  '{type_hint}' group was collapsed, expanding ...")
            time.sleep(1.2)
            client.refresh()
            node = pick_search_result(client, name, type_hint)
            if node is not None:
                log(f"  search result appeared after expanding the group (attempt {attempt})")
                break
        log(f"  attempt {attempt}/5: result not shown yet, waiting 1s ...")

    if node is None:
        sys.exit(f"no search result named '{name}' found in the UI tree after 5 attempts.")
    open_row(client, node, snapshot=f"search-result-{name}.json")
    log(f"opened search result '{name}'")


# -------------------------------------------------------------------- the bot

def cleanup(client):
    """close all windows opened during a run and clear the search box, restoring the initial state"""
    log("cleanup: closing open windows ...")
    for _ in range(30):  # hard cap; each pass closes one window
        wins = [w for _, lname, w in client.windows()
                if lname == "l_main" and w.type in ("InfoWindow", "ListWindow")]
        if not wins:
            break
        # close the topmost first: it may cover the close buttons of the others
        if not client.close_window(wins[0]):
            break
    input_node = find_search_input(client)
    if input_node is not None:
        client.click_node(input_node)
        time.sleep(0.4)
        client.send_keys("^a{DEL}")  # select all + delete
        log("cleanup: search box cleared")
    client.refresh()
    left = [w.type for _, lname, w in client.windows() if lname == "l_main"]
    log("cleanup: done, main-layer windows remaining: " + (", ".join(left) if left else "none"))


def load_baseline(region_name, mode="update"):
    """previous region report, used as the merge baseline (None = fresh walk).

    update mode: the newest report across the latest date directory plus up to
    report_store.LOOKBACK_DIRS older ones; a file from an earlier day is
    copied into today's directory first and updated in place from there.
    fresh mode: only today's own (partial) file counts, so an interrupted
    fresh run of this day resumes instead of rescanning from zero."""
    if mode == "fresh":
        p = report_store.today_dir() / report_store.report_filename(region_name)
        data = report_store.load_json(p) if p.exists() else None
    else:
        p, data = report_store.materialize_baseline(region_name)
        if p is not None:
            log(f"merge baseline: {p}")
    return data if data is not None and data.get("星座") else None


def save_report(report, region_name, complete):
    """write the region report into today's date directory; intermediate saves
    carry complete=False"""
    report["complete"] = complete
    report["星座数"] = len(report.get("星座", []))
    out = report_store.today_dir() / report_store.report_filename(region_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log(f"report saved to {out} (complete={complete}, 星座={len(report.get('星座', []))})")
    return out


def _collect_constellation(client, const_name, const_win, frag, report, region_name):
    """constellation showinfo window already open: reconcile its systems and
    buildings against the baseline fragment `frag` (in-place).

    Merge rules: buildings/systems already in the baseline keep their entry and
    are not clicked again; entries missing from the baseline are opened and
    extracted; baseline entries absent from the walked UI lists are removed."""
    base_systems = {s.get("名称"): s for s in frag.get("星系", [])}

    # 相关星系 list: only click the tab when its content is not already displayed
    systems = extract_systems(const_win)
    if not systems:
        if not click_tab(client, const_win, "相关星系", snapshot=f"const-{const_name}-systems-tab.json"):
            sys.exit(f"相关星系 tab not found in the constellation window {const_name}.")
        const_win = client.find_entity_window("星座：信息", const_name)
        if const_win is None:
            sys.exit("constellation window disappeared after activating the 相关星系 tab.")
        systems = extract_systems(const_win)
    log(f"constellation {const_name} lists {len(systems)} systems: {[s[1] for s in systems]}")

    frag["complete"] = False
    frag["星系"] = []
    all_systems_complete = True

    sys_stats = {}
    for sec, sysname, sysnode in iter_scroll_rows(
            client,
            lambda w: "星座：信息" in content_label(w) or "相关星系" in w.texts(),
            extract_systems, "systems", key_index=1, stats=sys_stats):
        base_s = base_systems.get(sysname)
        if base_s is not None and base_s.get("complete") is True:
            # verified end-to-end in an earlier run: keep the stored record,
            # no window is opened
            log(f"--- system {sysname}: already complete in report, skipping")
            frag["星系"].append(base_s)
            continue
        log(f"--- system {sysname} (security {sec}) ---")
        open_row(client, sysnode, snapshot=f"system-{sysname}.json")
        sys_win = client.find_entity_window("星系：信息", sysname)
        if sys_win is None:
            # a click swallowed by the closing animation of the previous
            # window: strays are cleared by now, try the same row once more
            log(f"  system window did not open for {sysname}, retrying once")
            open_row(client, sysnode)
            sys_win = client.find_entity_window("星系：信息", sysname)
        if sys_win is None:
            # transient click failure: keep whatever the baseline had for it
            log(f"  system window did not open for {sysname}, keeping previous data")
            if base_s is not None:
                frag["星系"].append(base_s)
                all_systems_complete = all_systems_complete and base_s.get("complete", True)
            else:
                log(f"  WARNING: {sysname} has no previous data and was not read")
                all_systems_complete = False
            continue

        # the showinfo may open with 建筑 already active (EVE remembers tabs):
        # only click the tab when the building list is not already displayed
        buildings = extract_buildings(sys_win)
        if not buildings and click_tab(client, sys_win, "建筑", snapshot=f"system-{sysname}-buildings-tab.json"):
            sys_win = client.find_entity_window("星系：信息", sysname)
            if sys_win is not None:
                buildings = extract_buildings(sys_win)
        if not buildings:
            log("  this system has no 建筑 tab or the list is empty, skipping")
        log(f"  {len(buildings)} building(s) in the list")

        base_buildings = {b.get("名称"): b for b in (base_s or {}).get("建筑", [])}
        sys_record = base_s if base_s is not None else {"名称": sysname, "安全等级": sec, "建筑数": 0, "建筑": []}
        sys_record["名称"] = sysname
        sys_record["安全等级"] = sec
        merged = []
        seen_ui = set()
        b_stats = {}
        for bname, bnode in iter_scroll_rows(
                client,
                lambda w: "星系：信息" in content_label(w) or "环绕天体" in w.texts(),
                extract_buildings, "buildings", key_index=0, stats=b_stats):
            seen_ui.add(bname)
            known = base_buildings.get(bname)
            if known is not None:
                merged.append(known)
                log(f"    building already in report, skipping: {bname[:52]}")
                continue
            open_row(client, bnode, snapshot=f"building-{bname[:20]}.json")
            bwin = client.find_entity_window("：信息", bname, attempts=2, close_mismatch=False)
            if bwin is None:
                log(f"  building window did not open for {bname}")
                continue
            full_caption, full_name, btype, owner = extract_building_details(bwin)
            log(f"  building: {full_name} | type={btype} | owner={owner}")
            merged.append({"名称": full_name, "类型": btype, "所属军团": owner})
            client.close_window(bwin)

        sys_record["建筑"] = merged
        sys_record["建筑数"] = len(merged)
        removed = [n for n in base_buildings if n not in seen_ui]
        if removed:
            log(f"    removed {len(removed)} building(s) no longer in the UI: {[n[:36] for n in removed[:5]]}")
        sys_record["complete"] = bool(b_stats.get("complete")) if buildings else True
        all_systems_complete = all_systems_complete and sys_record["complete"]
        frag["星系"].append(sys_record)
        client.close_window(sys_win)
        save_report(report, region_name, complete=False)

    frag["星系数"] = len(frag["星系"])
    # an early give-up of the systems walk (window lost / stuck scrollbar)
    # must not mark the fragment complete: unseen systems were not verified
    frag["complete"] = all_systems_complete and bool(sys_stats.get("complete"))


def _traverse_region(client, region_name, mode="update"):
    baseline = load_baseline(region_name, mode)
    base_consts = {c.get("名称"): c for c in (baseline or {}).get("星座", [])}
    if baseline:
        log(f"merge mode: baseline report found ({len(base_consts)} constellations)")

    # 1. search + open the region showinfo
    search_entity(client, region_name, type_hint="星域")
    region_win = client.find_entity_window("星域：信息", region_name)
    if region_win is None:
        sys.exit("region showinfo window did not open.")
    log(f"region window open: '{content_label(region_win)}'")

    # 2. 相关星座 list: EVE remembers the last active tab, so only click the tab
    #    when its content is not already displayed
    constellations = extract_list_rows(region_win)
    if not constellations:
        if not click_tab(client, region_win, "相关星座", snapshot="region-constellations-tab.json"):
            sys.exit("相关星座 tab not found in the region window.")
        region_win = client.find_entity_window("星域：信息", region_name)
        if region_win is None:
            sys.exit("region window disappeared after activating the 相关星座 tab.")
        constellations = extract_list_rows(region_win)
    log(f"region lists {len(constellations)} constellations: {[c[0] for c in constellations]}")

    report = baseline or {"星域": region_name, "星座数": 0, "星座": []}
    report["星域"] = region_name
    report["complete"] = False
    save_report(report, region_name, complete=False)

    new_consts = []
    const_stats = {}
    for const_name, const_node in iter_scroll_rows(
            client,
            lambda w: "星域：信息" in content_label(w) or "相关星座" in w.texts(),
            extract_list_rows, "constellations", key_index=0, stats=const_stats):
        log(f"=== constellation {const_name} ===")
        base_c = base_consts.get(const_name)
        if base_c is not None and base_c.get("complete") is True:
            # verified end-to-end in an earlier run and constellations are
            # static: keep the stored fragment, no window is opened
            log(f"    constellation {const_name} already complete in report, skipping")
            new_consts.append(base_c)
            continue
        frag = copy.deepcopy(base_c) if base_c else {
            "名称": const_name, "星系数": 0, "星系": [], "complete": False}
        frag["名称"] = const_name
        frag["complete"] = False
        # replace any previous entry with the working fragment
        report["星座"] = [c for c in report.get("星座", []) if c.get("名称") != const_name] + [frag]
        save_report(report, region_name, complete=False)

        open_row(client, const_node, snapshot=f"constellation-{const_name}.json")
        const_win = client.find_entity_window("星座：信息", const_name)
        if const_win is None:
            log(f"constellation window did not open for {const_name}, keeping previous data")
            new_consts.append(frag)
            continue
        try:
            _collect_constellation(client, const_name, const_win, frag, report, region_name)
        finally:
            # back to the region window for the next constellation
            const_win_now = client.find_window(lambda w: "星座：信息" in content_label(w) or "相关星系" in w.texts())
            if const_win_now is not None:
                client.close_window(const_win_now)
        new_consts.append(frag)

    report["星座"] = new_consts
    report["星座数"] = len(new_consts)
    region_complete = bool(const_stats.get("complete")) and all(c.get("complete") for c in new_consts)
    save_report(report, region_name, complete=region_complete)
    return report


def close_all_windows(client):
    """close every open InfoWindow/ListWindow so the run starts from a clean,
    unambiguous window stack (duplicate showinfo windows break scroll targeting)"""
    for _ in range(30):
        wins = [w for _, lname, w in client.windows()
                if lname == "l_main" and w.type in ("InfoWindow", "ListWindow")]
        if not wins:
            return
        if not client.close_window(wins[0]):
            return


def run(region_name, mode="update"):
    client = EveClient()
    log(f"EVE pid={client.pid}, window client origin={client.origin}, reading UI tree ...")
    client.refresh()
    try:
        close_all_windows(client)
        _traverse_region(client, region_name, mode)
    except SystemExit as e:
        log(f"run aborted: {e}")
        raise
    except KeyboardInterrupt:
        log("run interrupted by user (Ctrl+C)")
        raise
    except Exception:
        import traceback
        log("ERROR: " + traceback.format_exc().replace("\n", " | "))
        raise
    finally:
        # even on failure, restore the initial state for the next round
        cleanup(client)


# ------------------------------------------------------- offline extractor tests

def self_test():
    const_snap = TOOL_DIR / "snapshot-forge-region-mubuRuo-constellation-newcaldari-system.json"
    cit_snap = TOOL_DIR / "snapshot-forge-newcaldari-with-citadel-showinfo.json"

    print("== test 1: extract systems from constellation window snapshot ==")
    tree = load_tree(const_snap)
    client = EveClient.__new__(EveClient)  # no live process needed
    client.root = build_root(tree)
    const_win = client.find_window(lambda w: "相关星系" in w.texts())
    systems = extract_systems(const_win)
    for sec, name, _ in systems:
        print(f"   sec={sec}  {name}")
    assert len(systems) == 7, f"expected 7 systems, got {len(systems)}"

    print("== test 2: extract buildings from system window snapshot ==")
    sys_win = client.find_window(lambda w: "环绕天体" in w.texts())
    buildings = extract_buildings(sys_win)
    for name, _ in buildings:
        print(f"   {name}")
    assert len(buildings) == 4, f"expected 4 buildings, got {len(buildings)}"

    print("== test 3: extract building type from citadel showinfo snapshot ==")
    tree = load_tree(cit_snap)
    client.root = build_root(tree)
    cit_win = client.find_window(lambda w: "空堡" in json.dumps(w.raw, ensure_ascii=False))
    caption, name, btype, owner = extract_building_details(cit_win)
    print(f"   caption={caption}  name={name}  type={btype}  owner={owner}")
    assert btype == "堡垒 - 空堡", f"unexpected type: {btype}"

    print("\nall offline tests passed.")


def main(argv):
    global SAVE_SNAPSHOTS
    if "--test" in argv:
        self_test()
    elif "--cleanup" in argv:
        client = EveClient()
        client.refresh()
        cleanup(client)
    else:
        if "--save-snapshots" in argv:
            SAVE_SNAPSHOTS = True
            argv = [a for a in argv if a != "--save-snapshots"]
        report_store.migrate_legacy_reports()
        mode = report_store.prompt_mode() if "--fresh" not in argv and "--update" not in argv \
            else ("fresh" if "--fresh" in argv else "update")
        argv = [a for a in argv if a not in ("--fresh", "--update")]
        if len(argv) < 2:
            sys.exit(__doc__)
        run(argv[1], mode=mode)


if __name__ == "__main__":
    main(sys.argv)
