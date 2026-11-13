"""Summarise recorded map timings without importing or contacting instruments."""

import argparse
import csv
import io
import json
from pathlib import Path


def summarise(path):
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    comments = [json.loads(line[2:]) for line in lines if line.startswith("# ")]
    rows = list(
        csv.DictReader(
            io.StringIO("\n".join(line for line in lines if not line.startswith("#")))
        )
    )
    footer = next((item for item in reversed(comments) if "status" in item), {})
    result = {
        "pixels": len(rows),
        "status": footer.get("status", "footer_missing"),
        "elapsed_s": footer.get("elapsed_s"),
        "timing_totals_s": footer.get("timing_totals_s", {}),
        "mean_pixel_motion_s": None,
        "mean_pixel_acquisition_s": None,
        "raw_readings_per_pixel": sorted(
            {len(json.loads(row["readings_json"])) for row in rows}
        ),
    }
    if rows and "motion_s" in rows[0]:
        result["mean_pixel_motion_s"] = sum(
            float(row["motion_s"]) for row in rows
        ) / len(rows)
        result["mean_pixel_acquisition_s"] = sum(
            float(row["acquisition_s"]) for row in rows
        ) / len(rows)
    profile_path = Path(path).with_suffix(".motion.txt")
    if profile_path.exists():
        records = [
            json.loads(line)
            for line in profile_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        totals = {}
        for record in records:
            for key, value in record.items():
                if key.endswith("_s") and isinstance(value, (int, float)):
                    totals[key] = totals.get(key, 0.0) + value
        result["motion_profile"] = {
            "file": str(profile_path),
            "moves": len(records),
            "fallback_count": sum(r.get("fallback") is True for r in records),
            "failed_moves": sum(r.get("status") != "complete" for r in records),
            "command_count": sum(r.get("command_calls", 0) for r in records),
            "phase_totals_s": totals,
            "timing_basis": "Host call durations; fallback includes nested reads/commands. Not mechanical response times.",
        }
    from spectromagpythontools.routines.first_check_profile import enrich_summary

    enrich_summary(result, path)
    from spectromagpythontools.routines.feedback_summary import enrich_feedback_summary

    enrich_feedback_summary(result, path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarise(args.csv), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
