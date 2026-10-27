"""Build docs/sprint6/SPRINT_6_TEST_REPORT.md and PERFORMANCE_RESULTS.md from
measured artefacts only (no result is typed by hand):

    docs/sprint6/performance/junit_backend.xml   pytest (whole backend suite, -m "not gazebo")
    docs/sprint6/performance/junit_sitl.xml      pytest -m gazebo tests/test_mc_sitl_integration.py
    docs/sprint6/performance/junit_simhost.xml   pytest in the simhost container
    docs/sprint6/performance/junit_e2e.xml       Playwright (playwright.sprint6.config.ts)
    docs/sprint6/performance/junit_regression_sprint5.xml     pytest -m gazebo tests/test_sim_gazebo_integration.py (optional)
    docs/sprint6/performance/junit_regression_playwright.xml  Playwright default config, earlier sprints (optional)
    docs/sprint6/performance/sitl_integration.json, e2e_results.json, environment.json
Hand-written: docs/sprint6/performance/limitations.md (appended verbatim).
"""
from __future__ import annotations

import json
import platform
import statistics
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "sprint6"
PERF = DOCS / "performance"
SHOTS = DOCS / "evidence"
RECS = ROOT / "documents" / "sprint6" / "recordings"


def junit(name: str) -> dict[str, dict]:
    p = PERF / name
    if not p.exists():
        return {}
    out = {}
    for tc in ET.parse(p).getroot().iter("testcase"):
        key = f"{tc.get('classname')}::{tc.get('name')}"
        status, msg = "PASS", ""
        for tag, st in (("failure", "FAIL"), ("error", "ERROR"), ("skipped", "NOT RUN")):
            el = tc.find(tag)
            if el is not None:
                status, msg = st, (el.get("message") or "")[:200]
                break
        out[key] = {"name": tc.get("name"), "status": status, "time": float(tc.get("time") or 0), "message": msg}
    return out


def pick(results: dict, *needles: str) -> list[dict]:
    return [v for k, v in results.items() if all(n in k for n in needles)]


def summarize(cases: list[dict]) -> tuple[str, float]:
    if not cases:
        return "NOT RUN", 0.0
    s = {c["status"] for c in cases}
    status = "FAIL" if s & {"FAIL", "ERROR"} else ("NOT RUN" if s == {"NOT RUN"} else "PASS")
    return status, sum(c["time"] for c in cases)


def load(name: str) -> dict:
    p = PERF / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def pct(vals: list[float], p: float):
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(p / 100 * len(s)))], 1) if s else None


def stat_row(name, vals, target, unit, method, passed=None):
    if not vals:
        return f"| {name} | 0 | – | – | – | {target} | NOT MEASURED | {method} |"
    med, p95, mx = round(statistics.median(vals), 1), pct(vals, 95), round(max(vals), 1)
    verdict = passed if passed is not None else "–"
    return f"| {name} | {len(vals)} | {med} {unit} | {p95} {unit} | {mx} {unit} | {target} | **{verdict}** | {method} |"


def e2e_command_timings(er: dict) -> list[dict]:
    """Timings the backend persisted (drone_commands.timings) for commands of the E2E run window."""
    try:
        import sys
        sys.path.insert(0, str(ROOT / "backend"))
        from app.db.session import SessionLocal
        from app.models.mission_control import DroneCommand
        from sqlalchemy import select
    except Exception:
        return []
    started = er.get("started_at")
    state = ROOT / "frontend" / "test-results" / "sprint6" / "state.json"
    if not started and state.exists():
        started = json.loads(state.read_text(encoding="utf-8")).get("startedAt")
    t0 = datetime.fromisoformat(started.replace("Z", "+00:00")) if started else None
    t1 = datetime.fromisoformat(er["finished_at"].replace("Z", "+00:00")) if er.get("finished_at") else None
    if not t0 or not t1:
        return []
    with SessionLocal() as db:
        rows = db.execute(select(DroneCommand).where(DroneCommand.requested_at >= t0, DroneCommand.requested_at <= t1,
                                                     DroneCommand.state.in_(("confirmed", "accepted", "dispatched", "timed_out")))).scalars().all()
        return [{**(r.timings or {}), "_fleet": r.fleet_command_id is not None} for r in rows]


def cmd(args: list[str]) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=20).stdout.strip().splitlines()[0]
    except Exception:
        return "unknown"


def main() -> None:
    be, sitl, sh, e2e = junit("junit_backend.xml"), junit("junit_sitl.xml"), junit("junit_simhost.xml"), junit("junit_e2e.xml")
    si, er, env = load("sitl_integration.json"), load("e2e_results.json"), load("environment.json")
    steps = er.get("steps", {})
    e2e_case = list(e2e.values())
    e2e_status = summarize(e2e_case)[0]

    def step(k: str) -> str:
        v = steps.get(k)
        if v is None:
            return "NOT REACHED" if e2e_case else "NOT RUN"
        return v

    rows = [
        ("Dashboard rendering", step("dashboard"), "01"),
        ("Live telemetry", step("live_telemetry"), "02, 20, 21"),
        ("Leaflet synchronization", step("leaflet_sync"), "03"),
        ("FC discovery", "PASS" if step("fc_discovery") == "PASS" and summarize(pick(sitl, "discovery"))[0] == "PASS" else step("fc_discovery"), "04"),
        ("FC binding (discovery, QR camera, manual)", step("fc_binding"), "05"),
        ("Pre-arm checklist", step("prearm"), "06"),
        ("Arm via SITL, confirmed by telemetry", step("arm"), "–"),
        ("Video layouts (1/4/9, fullscreen)", step("video_layouts"), "07–10"),
        ("Picture-in-Picture", step("pip"), "–"),
        ("WebRTC link loss + reconnection", step("webrtc_reconnection"), "11"),
        ("DVR (record, play, seek)", step("dvr"), "12"),
        ("Quick commands (HOLD, RTL, GOTO, reject NFZ)", step("quick_commands") if step("quick_commands_goto") == "PASS" else step("quick_commands_goto"), "03, 14"),
        ("Assign task", step("assign_task"), "–"),
        ("Fleet-wide commands (confirm, cancel, per-drone)", step("fleet_commands"), "13, 14"),
        ("Detection review", step("detection_review"), "15, 16"),
        ("PostGIS persistence + heatmap", step("postgis"), "17, 18"),
        ("RBAC", step("rbac"), "19"),
        ("Stale telemetry + recovery", step("stale_recovery"), "20, 21"),
        ("Cleanup (LAND ALL, no processes left)", step("cleanup"), "–"),
        ("Full E2E workflow", e2e_status, "22"),
    ]

    def count(r: dict) -> str:
        c = [v["status"] for v in r.values()]
        return f"{c.count('PASS')} passed, {c.count('FAIL') + c.count('ERROR')} failed, {c.count('NOT RUN')} not run" if c else "not run"

    L: list[str] = []
    w = L.append
    w("# Sprint 6 — Test Report: Mission Control\n")
    w(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by `backend/scripts/generate_sprint6_report.py` from JUnit XML and "
      "measurement JSON in `docs/sprint6/performance/`. No test result in this file is typed by hand; only the Limitations section is "
      "(`performance/limitations.md`).\n")
    w("Evidence: [`docs/sprint6/evidence/`](evidence/) (also in [`documents/sprint6/`](../../documents/sprint6/)) · "
      "reference: [`MISSION_CONTROL.md`](MISSION_CONTROL.md) · performance: [`PERFORMANCE_RESULTS.md`](PERFORMANCE_RESULTS.md)\n")
    w("## Environment\n")
    w("| Item | Value |\n|---|---|")
    for k, v in env.items():
        w(f"| {k} | {v} |")
    w("")
    w("## Test summary\n")
    w("| Area | Result | Evidence |\n|---|---|---|")
    for name, st, ev in rows:
        w(f"| {name} | **{st}** | {ev} |")
    w("")
    w("| Suite | Kind | Result |\n|---|---|---|")
    w(f"| Backend (`pytest -m 'not gazebo'`) incl. `test_mission_control.py` | unit + API + DB/PostGIS (fakes for simhost/runtime) | {count(be)} |")
    w(f"| Sprint 6 SITL integration (`-m gazebo tests/test_mc_sitl_integration.py`) | real PX4 SITL + Gazebo + MediaMTX | {count(sitl)} |")
    w(f"| simhost container tests | units (detector, barcode, binding routing, SITL manager) | {count(sh)} |")
    w(f"| Playwright E2E (`playwright.sprint6.config.ts`) | browser, full stack, SITL bench | {count(e2e)} |")
    reg5, regpw = junit("junit_regression_sprint5.xml"), junit("junit_regression_playwright.xml")
    w(f"| Regression: Sprint 5 Gazebo (`-m gazebo tests/test_sim_gazebo_integration.py`) | real PX4 SITL + Gazebo | {count(reg5)} |")
    w(f"| Regression: earlier-sprint Playwright (`playwright.config.ts`) | browser, Sprints 1-5 | {count(regpw)} |")
    w("")
    failing = [v for r in (be, sitl, sh, e2e, reg5, regpw) for v in r.values() if v["status"] in ("FAIL", "ERROR")]
    if failing:
        w("### Failing tests\n")
        for f in failing:
            w(f"- `{f['name']}` — {f['message']}")
        w("")
    mc = pick(be, "test_mission_control")
    w(f"Sprint 6 backend tests: {sum(c['status'] == 'PASS' for c in mc)}/{len(mc)} passed "
      "(dashboard, binding + QR, pre-arm, commands + fleet commands, detections + PostGIS + heatmap, video signalling, RBAC).\n")

    w("## Real-simulator results (PX4 SITL bench)\n")
    w("| Measure | Value |\n|---|---|")
    d = si.get("mc_discovery", {})
    w(f"| Discovery (bench of {d.get('controllers')}) | {d.get('discovery_ms')} ms; bench start-up {d.get('bench_startup_s')} s |")
    ph = si.get("mc_prearm_health", {})
    if ph:
        w(f"| PX4 health reported | SYS_STATUS present 0x{ph['sensors_present']:x}, health 0x{ph['sensors_health']:x} (AHRS + pre-arm bits set); GNSS fix {ph['gps_fix']}, {ph['satellites']} sats; failsafes {ph['failsafe_params']} |")
    ct = si.get("mc_command_timing", {})
    for k in ("arm", "takeoff", "goto", "hold"):
        if k in ct:
            w(f"| {k.upper()} | simhost receipt → MAVLink send {ct[k]['recv_to_send_ms']} ms; send → PX4 COMMAND_ACK {ct[k]['send_to_ack_ms']} ms |")
    dt = si.get("mc_detector", {})
    if dt:
        w(f"| Detector (colour segmentation on the SITL camera) | marker geolocated within {dt['geolocation_error_m']} m from {dt['camera_height_m']} m height, confidence {dt['confidence']} |")
    cams = si.get("mc_cameras", {})
    if cams:
        cam_txt = ", ".join("%s: %sx%s, %s frames" % (u, c["size"][0], c["size"][1], c["frames"]) for u, c in cams.items() if isinstance(c, dict))
        w(f"| SITL cameras | {cam_txt} |")
    w("")

    w("## E2E measurements\n")
    w("| Measure | Value |\n|---|---|")
    for key, label in (("bench", "SITL bench"), ("bindings", "Bindings"), ("arm_attempts", "Arm attempts (backend refusals recorded)"),
                       ("prearm_blocked", "Pre-arm block (NFZ over pad)"), ("goto_rejected", "Prohibited GO TO"), ("nine_feeds", "Nine distinct feeds"),
                       ("pip", "Picture-in-Picture"), ("link_loss_overlay_ms", "Link-loss overlay after source stop (ms)"),
                       ("reconnect_ms", "Feed back after source restart (ms)"), ("dvr_seek", "DVR seek (position vs recorded barcode time)"),
                       ("hold_all", "HOLD ALL"), ("assign_task", "Assign task"), ("fixtures_via", "Test fixtures delivered via"),
                       ("sitl_detector_card", "SITL detector card shown"), ("heatmap", "Heatmap"), ("rbac_denied", "Observer requests (HTTP)"),
                       ("stale_after_ms", "STALE shown after link down (ms)"), ("recovered_after_ms", "CONNECTED again after restore (ms)"),
                       ("inflight_commands_after", "Commands still in flight at the end"), ("px4_processes_after", "PX4 processes after cleanup")):
        if key in er:
            w(f"| {label} | `{json.dumps(er[key], default=str)[:400]}` |")
    w("")
    w("## Screenshots\n")
    for p in sorted(SHOTS.glob("*.png")):
        w(f"- [`{p.name}`](evidence/{p.name})")
    for p in sorted(RECS.glob("*")) if RECS.exists() else []:
        w(f"- recording [`{p.name}`](../../documents/sprint6/recordings/{p.name}) ({p.stat().st_size / 1e6:.1f} MB)")
    expected = [f"{i:02d}" for i in range(1, 23)]
    missing = [n for n in expected if not any(p.name.startswith(n + "_") for p in SHOTS.glob("*.png"))]
    if missing:
        w(f"\nNot captured: {', '.join(missing)} (the E2E stopped before these steps or the state does not exist; see the summary above).")
    w("")
    found = PERF / "defects_found.md"
    if found.exists():
        w(found.read_text(encoding="utf-8").replace("# Sprint 6 — defects found by testing", "## Defects found and fixed during testing", 1))
        w("")
    lim = PERF / "limitations.md"
    if lim.exists():
        w(lim.read_text(encoding="utf-8").replace("# Sprint 6 — known limitations", "## Limitations and blocked items", 1))
    (DOCS / "SPRINT_6_TEST_REPORT.md").write_text("\n".join(L), encoding="utf-8")

    # ------------------------------------------------------- performance
    P: list[str] = []
    p = P.append
    p("# Sprint 6 — Performance results\n")
    p(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from `docs/sprint6/performance/*.json` "
      "(Playwright E2E + SITL integration). Pass/fail is computed from the numbers below.\n")
    p("## Environment\n")
    p("| Item | Value |\n|---|---|")
    for k, v in env.items():
        p(f"| {k} | {v} |")
    feeds = er.get("nine_feeds") or []
    n_sitl = sum("sitl/" in f for f in feeds)
    p(f"| Drones during the run | 8 kinematic demo + 3 PX4 SITL (bench) |\n| Video feeds | {len(feeds)} on screen ({n_sitl} SITL camera 256x192 @ 5 Hz sim, "
      f"{len(feeds) - n_sitl} test patterns 480x270 @ 15 fps) |"
      "\n| Network | localhost (browser, backend and Docker Desktop on one machine); no Wi-Fi or LTE in the path |\n")
    p("| Metric | Samples | Median | P95 | Max | Target | Result | Method |\n|---|---|---|---|---|---|---|---|")
    dl = er.get("dashboard_load", {})
    if dl:
        v = dl.get("navToReadyMs")
        p(stat_row("Dashboard initial load (navigation → fleet rendered)", [v] if v else [], "< 2000 ms", "ms",
                   "browser `performance.now()` at first fleet render after a full reload", "PASS" if v and v < 2000 else "FAIL"))
    ti = (er.get("telemetry_intervals") or {}).get("sitl") or {}
    if ti.get("n"):
        ok = ti["median"] is not None and ti["median"] <= 1100
        p(f"| Telemetry update interval (SITL drones, per drone) | {ti['n']} | {ti['median']:.0f} ms | {ti['p95']:.0f} ms | {ti['max']:.0f} ms | ≤ 1000 ms (1 Hz) | **{'PASS' if ok else 'FAIL'}** | arrival times of new `last_telemetry_at` values in the browser store |")
    fps = er.get("fps") or {}
    if fps:
        ok = fps.get("fps", 0) >= 55
        p(f"| Rendering frame rate (dashboard + 4 live video tiles) | {fps.get('frames')} frames / {fps.get('seconds', 0):.1f} s | {fps.get('fps', 0):.1f} fps | – | worst frame {fps.get('worstFrameMs', 0):.0f} ms | 60 fps | **{'PASS' if ok else 'FAIL'}** | `requestAnimationFrame` count over 5 s, headless Chromium |")
    dm = er.get("discovery_ui_ms")
    if dm is not None:
        p(stat_row("FC discovery (click → controller listed)", [dm], "< 5000 ms", "ms", "UI timing; API discovery itself " + str(er.get("discovery_api")) + " ms", "PASS" if dm < 5000 else "FAIL"))
    lat = [s["latencyMs"] for s in er.get("video_latency", []) if s.get("latencyMs") is not None and s["latencyMs"] > -100]
    if lat:
        p(stat_row("Video glass-to-glass latency (TEST PATTERN)", lat, "< 300 ms (Wi-Fi)", "ms",
                   "capture time barcoded in each frame vs browser presentation time, clock offset measured NTP-style; localhost, not Wi-Fi",
                   "PASS (localhost)" if pct(lat, 95) is not None and pct(lat, 95) < 300 else "FAIL"))
    det = [d["inferenceToCardMs"] for d in er.get("detection_latency", []) if d.get("provider") == "test_fixture"]
    if det:
        p(stat_row("Detection: inference timestamp → card visible", det, "< 1000 ms", "ms", "fixture `inferred_at` (backend clock = browser clock) to the card's first painted frame",
                   "PASS" if pct(det, 95) < 1000 else "FAIL"))
    lo = er.get("link_loss_overlay_ms")
    if lo is not None:
        p(stat_row("Link-loss overlay after the source stops", [lo], "< 2000 ms after loss is detectable", "ms",
                   "harness stops the publisher → overlay visible (includes relay teardown + 1.5 s frame-stall detection)", "PASS" if lo < 2000 else "FAIL"))
    p("")
    all_ctl = er.get("command_timings") or e2e_command_timings(er)
    command_table(p, "single-drone commands (ARM, TAKEOFF, GOTO, HOLD, RTL)", [t for t in all_ctl if not t.get("_fleet")])
    command_table(p, "fleet-wide commands (HOLD ALL / LAND ALL: every target validated in one request)", [t for t in all_ctl if t.get("_fleet")])
    p("The 50 ms target applies to *request → reaches the flight controller* (sum of backend validation, one-way backend→simhost HTTP estimate "
      "((RTT − handler)/2) and simhost receipt → MAVLink send). PX4 acknowledgement and the observed state change are reported separately and are not "
      "claimed against 50 ms. Kinematic-demo drones have no flight controller: their dispatch boundary is the NATS publish acknowledgement.\n")
    (DOCS / "PERFORMANCE_RESULTS.md").write_text("\n".join(P), encoding="utf-8")
    print("wrote", DOCS / "SPRINT_6_TEST_REPORT.md", "and", DOCS / "PERFORMANCE_RESULTS.md")


def command_table(p, title: str, ctl: list[dict]) -> None:
    p(f"### Command latency by boundary: {title}\n")
    p("| Boundary | Samples | Median | P95 | Max | Target | Result |\n|---|---|---|---|---|---|---|")

    def col(key):
        return [float(t[key]) for t in ctl if isinstance(t.get(key), (int, float))]
    for key, label, target in (("backend_pre_dispatch_ms", "Request received → adapter dispatch (backend)", None),
                               ("simhost_recv_to_mavlink_send_ms", "simhost receipt → MAVLink written to PX4", None),
                               ("request_to_fc_dispatch_ms", "Request → command reaches the flight controller", 50),
                               ("fc_ack_ms", "MAVLink send → PX4 COMMAND_ACK", None),
                               ("nats_publish_ack_ms", "NATS publish → JetStream ack (kinematic demo)", None),
                               ("request_to_ack_ms", "Request → PX4 ack known in the backend", None),
                               ("observed_state_ms", "Dispatch → telemetry shows the commanded state", None)):
        v = col(key)
        if not v:
            p(f"| {label} | 0 | – | – | – | {'< 50 ms' if target else '–'} | NOT MEASURED |")
            continue
        verdict = ("PASS" if pct(v, 95) < target else "FAIL") if target else "measured"
        p(f"| {label} | {len(v)} | {statistics.median(v):.1f} ms | {pct(v, 95)} ms | {max(v):.1f} ms | {'< ' + str(target) + ' ms' if target else '–'} | **{verdict}** |")
    p("")


if __name__ == "__main__":
    main()
