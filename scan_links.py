import json
import re
import sys


def ival(v):
    return v.get("int_low32") if isinstance(v, dict) and "int_low32" in v else v


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def collect_links(node, out):
    d = node.get("dictEntriesOfInterest", {})
    t = d.get("_setText")
    if isinstance(t, dict) and t.get("pythonObjectTypeName") == "Link":
        e = t.get("dictEntriesOfInterest", {})
        m = re.match(r"showinfo:(\d+)//(\d+)", e.get("_url") or "")
        if m:
            out.append({
                "type_id": int(m.group(1)),
                "entity_id": int(m.group(2)),
                "text": e.get("_text"),
                "alt": e.get("_alt"),
                "x": ival(d.get("_displayX")),
                "y": ival(d.get("_displayY")),
                "w": ival(d.get("_displayWidth")),
                "h": ival(d.get("_displayHeight")),
            })
    for c in node.get("children") or []:
        if c:
            collect_links(c, out)
    return out


if __name__ == "__main__":
    tree = load(sys.argv[1] if len(sys.argv) > 1 else "ui-tree.json")
    links = collect_links(tree, [])
    print("total showinfo links:", len(links))
    by_type = {}
    for l in links:
        by_type.setdefault(l["type_id"], []).append(l)
    for t, ls in sorted(by_type.items()):
        print(f"  type {t}: {len(ls)} links, e.g. {ls[0]['text']!r} alt={ls[0]['alt']!r}")

    consts = [l for l in links if l["type_id"] == 4]
    print()
    print("constellations in region window:")
    for l in consts:
        print(f"  {l['text']}  id={l['entity_id']}  pos=({l['x']},{l['y']}) {l['w']}x{l['h']}")
