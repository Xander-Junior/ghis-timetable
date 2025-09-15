from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: make_report.py <metrics.json> <presubmit.txt>")
        return 2
    m = Path(argv[1])
    p = Path(argv[2])
    try:
        metrics = json.loads(m.read_text(encoding="utf-8")) if m.exists() else {}
    except Exception:
        metrics = {}
    presubmit = p.read_text(encoding="utf-8") if p.exists() else ""
    html = [
        "<html><head><meta charset='utf-8'><style>body{font-family:Inter,Arial,sans-serif}pre{background:#f7f7f7;padding:8px;border:1px solid #eee}</style></head><body>",
        "<h2>Presubmit Report</h2>",
        "<h3>Metrics</h3>",
        f"<pre>{json.dumps(metrics, indent=2)}</pre>",
        "<h3>Presubmit Output</h3>",
        f"<pre>{presubmit}</pre>",
        "</body></html>",
    ]
    print("\n".join(html))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
