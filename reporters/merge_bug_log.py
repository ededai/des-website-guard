"""Conflict-free bug-log merge for concurrent site sweeps.

Each scheduled run sweeps trw then aura (matrix, max-parallel: 1). Both jobs
check out the run's *trigger* SHA, so when trw commits + pushes mid-run, aura
is still working from the pre-run base. A textual `git pull --rebase --autostash`
then collides on the end-of-file appends both jobs make to bug-log.jsonl, and the
loser's run fails (2026-07-12, -15).

The fix: merge by RECORD OWNERSHIP, not text. A sweep only ever changes its own
site's records, so the correct merged log is:

    [ origin's records for OTHER sites ] + [ this job's records for THIS site ]

Records are then written in a canonical order (first_seen, site, check_id) so trw
and aura produce byte-identical ordering for shared history — this keeps the git
diff minimal and stable instead of flipping order every commit.

Usage (from repo root, inside the "Commit updated bug log" step):
    python reporters/merge_bug_log.py --site aura \
        --origin bug-log.jsonl --job "$RUNNER_TEMP/bug-log.job.jsonl" \
        --out bug-log.jsonl
"""
import argparse
import json


def _load(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return out


def _sort_key(e):
    return (str(e.get("first_seen", "")), str(e.get("site", "")), str(e.get("check_id", "")))


def merge(site, origin_records, job_records):
    site = str(site).strip().lower()
    others = [e for e in origin_records if str(e.get("site", "")).lower() != site]
    mine = [e for e in job_records if str(e.get("site", "")).lower() == site]
    return sorted(others + mine, key=_sort_key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help="matrix site whose records this job owns (trw|aura)")
    ap.add_argument("--origin", required=True, help="origin/main's bug-log.jsonl (other sites' truth)")
    ap.add_argument("--job", required=True, help="this job's freshly-written bug-log.jsonl")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    origin = _load(a.origin)
    job = _load(a.job)
    merged = merge(a.site, origin, job)

    with open(a.out, "w", encoding="utf-8") as f:
        for e in merged:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    site = a.site.strip().lower()
    n_mine = sum(1 for e in merged if str(e.get("site", "")).lower() == site)
    print(f"merged: {len(merged)-n_mine} other-site + {n_mine} {site} = {len(merged)} records")


if __name__ == "__main__":
    main()
