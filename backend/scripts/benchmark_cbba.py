"""CBBA benchmark -- Sprint 3 acceptance: 8-drone AOI allocation < 2 s,
drone add / remove re-allocation < 2 s.

Run from backend/ (no database needed; it exercises the engine directly):

    python scripts/benchmark_cbba.py            # standard table
    python scripts/benchmark_cbba.py --repeat 10 --json

Scenarios are deterministic (app.services.coordination.scenarios): an
irregular ~0.6 km^2 AOI, 8 heterogeneous drones (camera / thermal /
multispectral / sprayer mixes, 58-95 % battery, 9-14 m/s, 22-35 min
endurance), and coverage tasks with mixed sensor requirements and priorities.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.coordination import AllocationProblem, CBBAEngine  # noqa: E402
from app.services.coordination.scenarios import benchmark_agents, benchmark_tasks  # noqa: E402

TARGET_S = 2.0


def _timed(fn):
    t0 = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - t0


def run_scenarios(task_counts: list[int], repeat: int) -> list[dict]:
    rows: list[dict] = []
    agents9 = benchmark_agents(9)
    eight, newcomer = agents9[:8], agents9[8]
    for n_tasks in task_counts:
        tasks = tuple(benchmark_tasks(n_tasks))
        base_problem = AllocationProblem(tuple(eight), tasks)
        removed_problem = AllocationProblem(tuple(a for a in eight if a.id != "DMX-004"), tasks)
        added_problem = AllocationProblem(tuple(eight + [newcomer]), tasks)

        cases = {
            "Allocate": lambda: CBBAEngine().allocate(base_problem),
        }
        base, _ = _timed(cases["Allocate"])
        cases["Remove drone"] = lambda: CBBAEngine().reallocate(removed_problem, base, removed_agent_ids=["DMX-004"])
        cases["Add drone"] = lambda: CBBAEngine().reallocate(added_problem, base, added_agents=[newcomer])
        drones = {"Allocate": "8", "Remove drone": "8->7", "Add drone": "8->9"}

        for name, fn in cases.items():
            samples = []
            result = None
            for _ in range(repeat):
                result, elapsed = _timed(fn)
                samples.append(elapsed)
            assert result is not None
            worst = max(samples)
            rows.append(
                {
                    "scenario": name,
                    "drones": drones[name],
                    "tasks": n_tasks,
                    "median_ms": round(statistics.median(samples) * 1000, 1),
                    "max_ms": round(worst * 1000, 1),
                    "iterations": result.iterations,
                    "converged": result.converged,
                    "assigned": result.assigned_count,
                    "unassigned": len(result.unassigned),
                    "makespan_s": round(result.makespan_s, 1),
                    "result": "PASS" if result.converged and worst < TARGET_S else "FAIL",
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", type=int, nargs="+", default=[20, 30, 50])
    parser.add_argument("--repeat", type=int, default=5, help="runs per scenario (max time is what must pass)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = run_scenarios(args.tasks, args.repeat)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        header = f"{'Scenario':<13}{'Drones':<8}{'Tasks':>6}{'Median ms':>11}{'Max ms':>9}{'Iter':>6}{'Assigned':>10}{'Unassigned':>12}{'Converged':>11}  Result"
        print(header)
        print("-" * len(header))
        for r in rows:
            print(
                f"{r['scenario']:<13}{r['drones']:<8}{r['tasks']:>6}{r['median_ms']:>11}{r['max_ms']:>9}{r['iterations']:>6}"
                f"{r['assigned']:>10}{r['unassigned']:>12}{str(r['converged']):>11}  {r['result']}"
            )
        print(f"\nTarget: every scenario converges in < {TARGET_S:.0f} s (worst of {args.repeat} runs).")
    return 0 if all(r["result"] == "PASS" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
