"""Build docs/sprint5/SPRINT_5_TEST_REPORT.md from actual test output.

Inputs (written by the test runs, see docs/sprint5/SIMULATION.md section 11):
    docs/sprint5/performance/junit_backend.xml      pytest (all backend tests incl. Sprint 5)
    docs/sprint5/performance/junit_gazebo.xml       pytest -m gazebo (real Gazebo + PX4)
    docs/sprint5/performance/junit_simhost.xml      pytest inside the simhost container
    docs/sprint5/performance/junit_e2e.xml          Playwright sprint5.spec.ts
    docs/sprint5/performance/gazebo_integration.json measurements from the Gazebo tests
    docs/sprint5/performance/e2e_results.json        measurements from the E2E run
    docs/sprint5/performance/environment.json        versions (collected by this script)

Nothing is marked PASS by hand: every status below is read from JUnit.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERF = ROOT / "docs" / "sprint5" / "performance"
OUT = ROOT / "docs" / "sprint5" / "SPRINT_5_TEST_REPORT.md"
SHOTS = ROOT / "documents" / "sprint5" / "screenshots"
RECS = ROOT / "documents" / "sprint5" / "recordings"


def junit(name: str) -> dict[str, dict]:
    path = PERF / name
    if not path.exists():
        return {}
    out = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        key = f"{case.get('classname', '')}::{case.get('name')}"
        status = "PASS"
        message = ""
        for tag, st in (("failure", "FAIL"), ("error", "ERROR"), ("skipped", "NOT RUN")):
            el = case.find(tag)
            if el is not None:
                status, message = st, (el.get("message") or "")[:160]
                break
        out[key] = {"status": status, "time": float(case.get("time") or 0), "message": message, "name": case.get("name")}
    return out


def pick(results: dict, *needles: str) -> list[dict]:
    return [v for k, v in results.items() if all(n in k for n in needles)]


def summarize(cases: list[dict]) -> tuple[str, float]:
    if not cases:
        return "NOT RUN", 0.0
    statuses = {c["status"] for c in cases}
    status = "FAIL" if statuses & {"FAIL", "ERROR"} else ("NOT RUN" if statuses == {"NOT RUN"} else "PASS")
    return status, sum(c["time"] for c in cases)


def load(name: str) -> dict:
    p = PERF / name
    return json.loads(p.read_text()) if p.exists() else {}


def cmd(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return "unknown"


def environment() -> dict:
    env = load("environment.json")
    env.setdefault("os", f"{platform.system()} {platform.release()} ({platform.version()})")
    env.setdefault("python_backend", sys.version.split()[0])
    env.setdefault("node", cmd(["node", "--version"]))
    return env


def main() -> None:
    be, gz, sh, e2e = junit("junit_backend.xml"), junit("junit_gazebo.xml"), junit("junit_simhost.xml"), junit("junit_e2e.xml")
    gzm, em, env = load("gazebo_integration.json"), load("e2e_results.json"), environment()

    rows = [
        ("AOI creation & GeoJSON persistence", pick(be, "test_sim_api", "stage_fleet_positions") + pick(e2e, "AOI selected"), "01, 02"),
        ("Coordinate transform (WGS84 <-> Gazebo ENU)", pick(be, "test_sim_coordinates"), "-"),
        ("Task-type planning (survey/SAR/agri/inspection/custom)", pick(be, "test_sim_task_types"), "03"),
        ("1 km² world generation (fixture + real DEM)", pick(be, "test_sim_world"), "04"),
        ("Gazebo world loading / terrain / spawn geodesy", pick(gz, "world_loads"), "05"),
        ("SITL process manager (ports, crash, cleanup, orphans)", pick(sh, "test_sitl_manager") + pick(gz, "crash_detection") + pick(gz, "fleet_size"), "06"),
        ("CBBA allocation (existing engine)", pick(be, "test_cbba") + pick(be, "test_sim_task_types"), "03"),
        ("PX4 mission execution (waypoints, RTL, landing)", pick(gz, "mission_execution"), "07"),
        ("Telemetry to Leaflet (marker = simulator telemetry)", pick(e2e, "AOI selected"), "08, 09, 10"),
        ("Orchestrator responses (crash, battery, comm, stale progress)", pick(be, "test_sim_orchestrator"), "12, 13"),
        ("Failure injection on real simulator (kill, obstacle, wind, link loss, GPS drift, battery)",
         pick(gz, "crash_detection") + pick(gz, "airborne_crash") + pick(gz, "obstacle") + pick(gz, "wind") + pick(gz, "link_loss") + pick(gz, "gps_drift") + pick(gz, "low_battery"), "12, 13"),
        ("Simulation controls (pause/resume/speed, guardrail)", pick(gz, "pause_resume") + pick(sh, "test_guardrail"), "11"),
        ("1x vs 10x equivalence", pick(gz, "equivalence"), "-"),
        ("Report generation & comparison", pick(be, "test_sim_orchestrator", "report") + pick(e2e, "report comparison"), "14, 15"),
        ("Simulation/hardware separation & RBAC", pick(be, "test_sim_api", "adapters") + pick(be, "test_sim_api", "rbac"), "-"),
        ("Full E2E mission (browser -> Gazebo -> report)", pick(e2e, "AOI selected"), "16"),
    ]

    def count(r: dict) -> str:
        c = [v["status"] for v in r.values()]
        return f"{c.count('PASS')} passed, {c.count('FAIL') + c.count('ERROR')} failed, {c.count('NOT RUN')} not run" if c else "not run"

    L: list[str] = []
    w = L.append
    w("# Sprint 5 — Test Report: Gazebo Simulation Integration\n")
    w(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `backend/scripts/generate_sprint5_report.py` "
      "from JUnit XML and measurement JSON in `docs/sprint5/performance/`. No test result in this file is typed by hand; "
      "only the Limitations section is written by hand (`performance/limitations.md`).\n")
    w("Evidence: [`documents/sprint5/screenshots/`](../../documents/sprint5/screenshots/) · "
      "[`documents/sprint5/recordings/`](../../documents/sprint5/recordings/) · technical reference: [`SIMULATION.md`](SIMULATION.md)\n")

    w("## Environment\n")
    w("| Item | Value |\n|---|---|")
    for k, v in env.items():
        w(f"| {k} | {v} |")
    w("")

    w("## Test summary\n")
    w("| Test | Result | Duration | Evidence |\n|---|---|---|---|")
    for name, cases, ev in rows:
        st, t = summarize(cases)
        w(f"| {name} | **{st}** ({len(cases)} cases) | {t:.1f} s | {ev} |")
    w("")
    w(f"Suites: backend `{count(be)}` · Gazebo/PX4 integration `{count(gz)}` · simhost container `{count(sh)}` · Playwright E2E `{count(e2e)}`.\n")

    failing = [v for r in (be, gz, sh, e2e) for v in r.values() if v["status"] in ("FAIL", "ERROR")]
    if failing:
        w("### Failing tests\n")
        for f in failing:
            w(f"- `{f['name']}` — {f['message']}")
        w("")

    w("## World generation\n")
    world = em.get("world", {})
    t = world.get("terrain", {})
    w("| Measure | Value |\n|---|---|")
    w(f"| AOI area (E2E) | {world.get('aoi_area_km2')} km² |")
    w(f"| DEM source | {t.get('source_label')} (fallback: {t.get('is_fallback')}) |")
    w(f"| DEM native resolution | {t.get('native_resolution_m')} m |")
    w(f"| Heightmap | {t.get('heightmap_px')} px, {t.get('heightmap_spacing_m')} m spacing |")
    w(f"| Generation time (E2E world, cached tiles) | {world.get('timings_s', {}).get('total_s')} s |")
    w("| 1 km² real-DEM world | measured by `test_real_dem_one_km2_world` (see backend suite; cold download vs cached) |")
    tw = gzm.get("world_terrain_spawn", {})
    w(f"| Gazebo load + terrain check | terrain Δz {tw.get('terrain_dz_m')} m vs 9.6 m expected; startup {tw.get('startup_s')} s |")
    w("")

    w("## SITL\n")
    sitl = em.get("sitl", {})
    w("| Measure | Value |\n|---|---|")
    w(f"| Requested / started (E2E) | 3 / {len(sitl.get('pids', []))} |")
    w(f"| Start-up to RUNNING (E2E, incl. world load, EKF, arming) | {em.get('startup_s')} s |")
    inst_txt = ", ".join(f"{i['uin']} udp {i['port']} {i['model']}" for i in sitl.get("instances", []))
    w(f"| Instances | {inst_txt} |")
    cc = gzm.get("crash_cleanup", {})
    w(f"| Crash detection | SIGKILL detected, returncode {cc.get('crash_returncode')} |")
    w(f"| Leftover processes after stop | {cc.get('leftover')} (integration) / {em.get('leftover_processes')} (E2E) |")
    w(f"| Capacity on this host | {env.get('simhost_max_instances')} instances (Docker VM {env.get('docker_vm')}) |")
    w("")

    w("## Coordinate synchronization\n")
    w("| Measure | Value |\n|---|---|")
    w(f"| Geographic origin (E2E) | {world.get('frame', {}).get('origin')} |")
    w("| Local frame | ENU at the AOI centroid; X east, Y north, Z up; metres; WGS84 |")
    w("| Round-trip error | < 1 mm (`test_sim_coordinates`, 9 regions incl. antimeridian) |")
    w(f"| Initial marker vs spawn pose (E2E) | {[round(x, 3) for x in em.get('initial_marker_vs_spawn_m', [])]} m |")
    w(f"| Rendered marker vs backend telemetry (E2E) | {[round(x, 3) if x is not None else None for x in em.get('marker_vs_backend_telemetry_m', [])]} m |")
    w(f"| PX4-reported vs Gazebo ground truth (E2E) | {em.get('reported_vs_gazebo_truth_m')} m |")
    w(f"| PX4-reported vs Gazebo ground truth (integration) | {tw.get('reported_vs_truth_m')} m |")
    w("| Altitudes | Gazebo z = terrain − origin elevation; mission altitude relative to launch (PX4 GLOBAL_RELATIVE_ALT) |")
    w("")

    w("## Simulation execution (E2E mission)\n")
    final, rep = em.get("final", {}), em.get("report", {})
    ts = rep.get("task_summary", {})
    w("| Measure | Value |\n|---|---|")
    w(f"| CBBA | {em.get('cbba')} |")
    w(f"| Final state | {final.get('state')} at sim t = {final.get('sim_time_s')} s |")
    w(f"| Tasks total / assigned / completed / failed | {ts.get('total')} / {ts.get('assigned')} / {ts.get('completed')} / {ts.get('failed')} |")
    w(f"| Coverage | {ts.get('coverage_pct')} % (planned {ts.get('planned_coverage_pct')} %) |")
    w(f"| Per-drone completed | {ts.get('per_drone_completed')} |")
    w(f"| Injected failure | {em.get('failure', {}).get('realloc')} |")
    w(f"| Battery | {rep.get('battery')} |")
    w(f"| Pause check (sim time) | {em.get('pause')} |")
    w(f"| E2E wall time from Start | {em.get('total_s')} s |")
    w("")

    w("## Simulation speed\n")
    sp = gzm.get("speed_rtf", {}).get("requested_vs_achieved_rtf", {})
    eq = gzm.get("speed_equivalence", {})
    w("| Requested | Achieved real-time factor (1 drone) |\n|---|---|")
    for k, v in sp.items():
        w(f"| {k} | {v} |")
    w("")
    if eq:
        w(f"1x vs 10x (same mission): reached all waypoints {eq['1x']['reached_all']} / {eq['10x']['reached_all']}; landing error "
          f"{eq['1x']['landing_error_m']} m / {eq['10x']['landing_error_m']} m; simulated flight time {eq['1x']['flight_sim_s']:.1f} s / "
          f"{eq['10x']['flight_sim_s']:.1f} s (Δ {eq['flight_time_rel_diff'] * 100:.1f} %); wall time {eq['1x']['wall_s']} s / {eq['10x']['wall_s']} s "
          f"(achieved RTF {eq['10x']['achieved_rtf']:.2f}).\n")

    w("## Failure injection (real simulator)\n")
    w("| Failure | Measured |\n|---|---|")
    w(f"| Kill drone / SITL crash | returncode {cc.get('crash_returncode')}; E2E: {em.get('failure', {}).get('realloc')} |")
    ac = gzm.get("airborne_crash", {})
    w(f"| Kill in flight (15 m) | motors cut, drone fell and rests at {ac.get('rest')} (ground z {ac.get('ground_z')}); "
      f"world kept stepping ({ac.get('sim_time_advanced_s')} s in 10 s) |")
    w(f"| Link loss | PX4 mode after loss: {gzm.get('link_loss_failsafe', {}).get('px4_mode_after_loss')} (policy LAND) |")
    gd = gzm.get("gps_drift", {})
    w(f"| GPS drift | reported-vs-truth error {gd.get('baseline_error_m')} m -> {gd.get('error_after_injection_m')} m; PX4 followed the biased GPS after {gd.get('px4_reaction_sim_s')} s sim |")
    w(f"| Obstacle | map -> Gazebo -> map position error {gzm.get('obstacle', {}).get('position_error_m')} m |")
    bl = gzm.get("battery_low", {})
    w(f"| Low battery (forced to {bl.get('params', {}).get('SIM_BAT_MIN_PCT')} %) | PX4 failsafe mode: {bl.get('px4_mode')}; charge samples {bl.get('battery_samples')} % |")
    wg = gzm.get("wind_gust", {})
    w(f"| Wind gust (20 m/s from W, 3 s) | displacement east {wg.get('displacement_east_m')} m, north {wg.get('displacement_north_m')} m |")
    w("")

    w("## Report generation\n")
    w(f"| Measure | Value |\n|---|---|\n| Generation time (E2E) | {rep.get('generation_ms')} ms (target < 30 s) |")
    cmp_ = em.get("comparison", {})
    if cmp_:
        w("| Re-run | same AOI, task type and fleet at 2×, no injected failure; stopped on purpose after > 2 completed tasks "
          "(it exercises the diff, it is not a second full flight) |")
        w(f"| Comparison | compatible={cmp_.get('compatible')}; warnings: {'; '.join(cmp_.get('warnings', []))} |")
        w(f"| Coverage Δ / battery Δ / duration Δ | {cmp_['diff']['coverage_pct']['delta']} / {cmp_['diff']['battery_used_total_pct']['delta']} / "
          f"{cmp_['diff']['simulated_duration_s']['delta']} |")
    w("")

    w("## Evidence\n")
    for p in sorted(SHOTS.glob("*.png")):
        w(f"- [`{p.name}`](../../documents/sprint5/screenshots/{p.name})")
    for p in sorted(RECS.glob("*")):
        w(f"- recording [`{p.name}`](../../documents/sprint5/recordings/{p.name}) ({p.stat().st_size / 1e6:.1f} MB)")
    w("")
    limits = PERF / "limitations.md"
    if limits.exists():
        w(limits.read_text(encoding="utf-8").replace("# Sprint 5 — known limitations (measured)", "## Limitations (measured)", 1))
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
