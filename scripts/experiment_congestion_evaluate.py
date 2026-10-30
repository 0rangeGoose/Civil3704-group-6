#!/usr/bin/env python3
"""Step 6 experiment, part 2 of 2: for each baseline (night / midday) and stretch
length, fit TfNSW-matched thresholds on Mon-Wed peaks and score agreement on
Thu-Fri -> data/analysis/experiment_results.json. Run from the repo root."""
import importlib.util, json, sys
from collections import defaultdict
from datetime import date
from pathlib import Path
sys.path.insert(0, ".")
spec = importlib.util.spec_from_file_location("ac", "scripts/analyse_congestion.py")
ac = importlib.util.module_from_spec(spec); spec.loader.exec_module(ac)

rows = json.loads(Path("data/analysis/experiment_raw.json").read_text())
TRAIN_DAYS = {"2026-10-26", "2026-10-27", "2026-10-28"}
SLOT = 600


def p85(v):
    v = sorted(v); return v[int(0.85 * (len(v) - 1))]


def baseline(kind, L):
    seg, shp = defaultdict(list), defaultdict(list)
    for k, w, l, shape, idx, at, sp, _ in rows:
        if k == kind and l == L:
            seg[(shape, idx)].append(sp); shp[shape].append(sp)
    return ({k: p85(v) for k, v in seg.items() if len(v) >= 3}, {k: p85(v) for k, v in shp.items() if len(v) >= 5})


def fit_thresholds(pairs):
    """Quantile-match: our level shares = TfNSW's level shares on the training peaks."""
    n = len(pairs)
    share = [sum(1 for _, t in pairs if t == lv) / n for lv in range(4)]
    tti = sorted(x for x, _ in pairs)
    cum, out = 0.0, []
    for lv in range(3):
        cum += share[lv]
        out.append(round(tti[min(n - 1, int(cum * n))], 2))
    return tuple(out), [round(s, 3) for s in share]


def level(x, th):
    return sum(x > t for t in th)


results = []
for kind in ("night", "midday"):
    for L in (300, 600, 1000):
        seg, shp = baseline(kind, L)
        peak = [r for r in rows if r[0] == "peak" and r[2] == L]
        scored = []
        for _, w, _, shape, idx, at, sp, tf in peak:
            free = seg.get((shape, idx)) or shp.get(shape)
            if free and sp >= 0.3:
                scored.append((w, (shape, idx), at // SLOT, min(10.0, free / sp), tf))
        coverage = len(scored) / max(1, len(peak))
        labelled = [s for s in scored if s[4] is not None]
        train = [(s[3], s[4]) for s in labelled if s[0][:10] in TRAIN_DAYS]
        test = [s for s in labelled if s[0][:10] not in TRAIN_DAYS]
        th, share = fit_thresholds(train)
        per_reading = ac.agreement([(level(s[3], th), s[4]) for s in test])
        slots = defaultdict(lambda: ([], []))
        for s in test:
            slots[(s[0], s[1], s[2])][0].append(s[3]); slots[(s[0], s[1], s[2])][1].append(s[4])
        per_slot = ac.agreement([(level(sum(a) / len(a), th), round(sum(b) / len(b))) for a, b in slots.values()])
        streets = defaultdict(lambda: ([], []))
        for s in test:
            streets[(s[0], s[1])][0].append(s[3]); streets[(s[0], s[1])][1].append(s[4])
        big = [(sum(a) / len(a), sum(b) / len(b)) for a, b in streets.values() if len(a) >= 10]
        results.append({
            "baseline": kind, "stretch_m": L, "coverage": round(coverage, 2), "thresholds": th, "tfnsw_shares": share,
            "test_reading_kappa": per_reading.get("weighted_kappa"), "test_reading_exact": per_reading.get("exact"),
            "test_reading_spearman": ac.spearman([s[3] for s in test], [s[4] for s in test]),
            "test_slot_kappa": per_slot.get("weighted_kappa"), "test_slot_exact": per_slot.get("exact"),
            "test_street_peak_spearman": ac.spearman([a for a, _ in big], [b for _, b in big]), "streets": len(big),
            "n_test": len(test),
        })

print(f"{'baseline':8} {'L':>5} {'cover':>6} {'thresholds':>18} {'rd kappa':>9} {'rd exact':>9} {'rd rho':>7} {'slot kappa':>11} {'street rho':>11} {'streets':>8}")
for r in results:
    print(f"{r['baseline']:8} {r['stretch_m']:>5} {r['coverage']:>6.0%} {str(r['thresholds']):>18} {r['test_reading_kappa']:>9} {r['test_reading_exact']:>9.0%} {r['test_reading_spearman']:>7} {r['test_slot_kappa']:>11} {r['test_street_peak_spearman']:>11} {r['streets']:>8}")
print("TfNSW level shares (train):", results[0]["tfnsw_shares"])
Path("data/analysis/experiment_results.json").write_text(json.dumps(results, indent=2))
