"""Render an Archimedes JSONL execution trace as a standalone HTML report."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


def load_events(path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number} is not a JSON object")
        events.append(value)
    if not events:
        raise ValueError("trace contains no events")
    return events


def render(events: list[dict[str, object]]) -> str:
    transitions = [event for event in events if event.get("action") == "state_transition"]
    flow = "".join(
        f'<span class="node">{html.escape(str(event.get("stage", "unknown")))}</span>'
        + ('<span class="arrow">→</span>' if index + 1 < len(transitions) else "")
        for index, event in enumerate(transitions)
    )
    rows = []
    for event in events:
        attributes = json.dumps(event.get("attributes", {}), sort_keys=True, ensure_ascii=False)
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(event.get('sequence', '')))}</td>"
            f"<td>{html.escape(str(event.get('elapsed_ms', '')))}</td>"
            f"<td>{html.escape(str(event.get('stage', '')))}</td>"
            f"<td>{html.escape(str(event.get('action', '')))}</td>"
            f"<td><code>{html.escape(attributes)}</code></td>"
            "</tr>"
        )
    first = events[0]
    title = f"Archimedes trace — {first.get('case_id', 'unknown case')}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(title)}</title><style>
:root {{ color-scheme: light dark; font-family: ui-sans-serif, system-ui, sans-serif; }}
body {{ max-width: 1200px; margin: 2rem auto; padding: 0 1rem; }}
.flow {{ display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; margin:1.5rem 0; }}
.node {{ border:1px solid #758195; border-radius:.5rem; padding:.55rem .8rem; }}
.arrow {{ color:#758195; }} table {{ width:100%; border-collapse:collapse; }}
th,td {{ border-bottom:1px solid #75819555; padding:.55rem; text-align:left; vertical-align:top; }}
th {{ position:sticky; top:0; background:Canvas; }} code {{ white-space:pre-wrap; word-break:break-word; }}
</style></head><body><h1>{html.escape(title)}</h1>
<p>Trace ID: <code>{html.escape(str(first.get('trace_id', 'unknown')))}</code> · {len(events)} events</p>
<div class="flow">{flow}</div>
<table><thead><tr><th>#</th><th>ms</th><th>stage</th><th>action</th><th>attributes</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path, default=Path("dev/trace.html"))
    args = parser.parse_args()
    events = load_events(args.trace)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(events), encoding="utf-8")
    print(f"rendered {len(events)} events to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
