# Extract the EVE location info panel ("星域:信息") from a Sanderling memory reading JSON.
# Usage: python extract-location-panel.py [ui-tree.json] [-o info-panel-location.json]
import json
import re
import sys


def load_tree(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_panel(node):
    if node.get("pythonObjectTypeName") == "InfoPanelLocationInfo":
        return node
    for child in node.get("children") or []:
        if child:
            found = find_panel(child)
            if found:
                return found
    return None


def strip_markup(text):
    """Remove EVE's flavor markup (hint/color/fontsize tags) but keep link text."""
    text = re.sub(r"<a href=[^>]*>(.*?)</a>", r"\1", text)
    text = re.sub(r"<url=[^>]*>(.*?)</url>", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def get_label_text(node):
    """Label text lives in the _setText entry: either a plain string or a Link object."""
    value = node.get("dictEntriesOfInterest", {}).get("_setText")
    if isinstance(value, dict):  # Link object: url + plain text + alt
        entries = value.get("dictEntriesOfInterest", {})
        return {"url": entries.get("_url"), "text": entries.get("_text"), "alt": entries.get("_alt")}
    if isinstance(value, str):
        return {"text": strip_markup(value)}
    return None


def ival(value):
    return value.get("int_low32") if isinstance(value, dict) and "int_low32" in value else value


def summarize(panel):
    labels = {}

    def walk(node):
        entries = node.get("dictEntriesOfInterest", {})
        name = entries.get("_name")
        if name:
            text = get_label_text(node)
            if text:
                labels[name] = text
        for child in node.get("children") or []:
            if child:
                walk(child)

    walk(panel)

    def entry(name):
        return labels.get(name, {}).get("text")

    return {
        "solar_system": entry("headerLabelSystemName"),
        "security_status": entry("headerLabelSecStatus"),
        "constellation_and_region": entry("headerLabelLocationTrace"),
        "nearest_location": entry("nearestLocationInfo"),
        "other_labels": {k: v for k, v in labels.items() if not k.startswith("headerLabel") and k != "nearestLocationInfo"},
        "display_region": {
            "x": ival(panel.get("dictEntriesOfInterest", {}).get("_displayX")),
            "y": ival(panel.get("dictEntriesOfInterest", {}).get("_displayY")),
            "width": ival(panel.get("dictEntriesOfInterest", {}).get("_displayWidth")),
            "height": ival(panel.get("dictEntriesOfInterest", {}).get("_displayHeight")),
        },
    }


if __name__ == "__main__":
    input_path = next((a for a in sys.argv[1:] if a.endswith(".json") and not a.startswith("-o")), "ui-tree.json")
    output_index = sys.argv.index("-o") + 1 if "-o" in sys.argv else None
    output_path = sys.argv[output_index] if output_index else "info-panel-location.json"

    tree = load_tree(input_path)
    panel = find_panel(tree)
    if panel is None:
        sys.exit("InfoPanelLocationInfo not found in " + input_path)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(panel, f, ensure_ascii=False, indent=2)

    print(json.dumps(summarize(panel), ensure_ascii=False, indent=2))
    print("\nFull panel subtree saved to " + output_path)
