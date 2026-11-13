"""Summarise nested feedback-read timings without hardware access."""

import json
from pathlib import Path


def enrich_feedback_summary(result, csv_path):
    path = Path(csv_path).with_suffix(".motion.txt")
    if not path.exists():
        return
    reads = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            reads.extend(json.loads(line).get("feedback_reads", []))
    if not reads:
        return
    result["feedback_read_summary"] = {
        "reads": len(reads),
        "combined_reads": sum(r["mode"] == "combined" for r in reads),
        "legacy_reads": sum(r["mode"] == "legacy" for r in reads),
        "settings_refresh_reads": sum(bool(r["refresh_settings"]) for r in reads),
        "status_requests": sum(r["status_requests"] for r in reads),
        "actual_position_requests": sum(r["actual_position_requests"] for r in reads),
        "requests_s": sum(r["requests_s"] for r in reads),
        "response_wait_s": sum(r["response_wait_s"] for r in reads),
        "getters_s": sum(r["getters_s"] for r in reads),
        "timing_basis": "Nested within existing move timings; do not add to motion total. Cached SDK values, not response acknowledgements.",
    }

    groups = {}
    api_totals = {}
    for read in reads:
        label = "settings_refresh" if read["refresh_settings"] else "ordinary_motion"
        group = groups.setdefault(
            label,
            {"reads": 0, "requests_s": 0.0, "response_wait_s": 0.0, "getters_s": 0.0},
        )
        group["reads"] += 1
        for key in ("requests_s", "response_wait_s", "getters_s"):
            group[key] += read[key]
        for call in read.get("request_calls", []):
            total = api_totals.setdefault(call["api"], {"calls": 0, "elapsed_s": 0.0})
            total["calls"] += 1
            total["elapsed_s"] += call["elapsed_s"]
    result["feedback_read_summary"]["by_read_type"] = groups
    result["feedback_read_summary"]["by_request_api"] = api_totals
    result["feedback_read_summary"]["request_calls_recorded"] = sum(
        item["calls"] for item in api_totals.values()
    )
