# Sprint 6 — defects found by testing

Real defects that the E2E and regression runs exposed. Each one was fixed in the product (not worked around in a test) before the reported runs.

| # | Found by | Defect | Fix |
|---|---|---|---|
| 1 | E2E | Telemetry freshness used the producer's timestamp, so a drone whose clock ran ahead or behind was shown as live or stale wrongly. | Freshness is measured by backend receive time (`received_at`), sent with every WebSocket frame. |
| 2 | E2E | The overview loaded up to 500 detections including thumbnails, and synchronous drone-row writes delayed the telemetry stream. | Pending count only, thumbnails fetched on demand, drone-row writes coalesced in the background. |
| 3 | E2E | `ConnectionManager` dropped a socket whose send failed but never closed it: the browser stayed connected to a stream that no longer sent anything. | Per-connection send locks, a 5 s send timeout, and failed sockets are closed so the client reconnects. |
| 4 | E2E | Command states and detections pushed while the WebSocket was reconnecting were lost. | The dashboard re-reads them after every reconnect and polls in-flight commands every 2 s. |
| 5 | E2E | The WHEP player could stay in RECONNECTING after a media failure (an early return skipped the retry). | The retry always runs, guarded only against a superseded peer connection. |
| 6 | E2E | PX4 auto-disarms ~10 s after arming without take-off, so ARM looked like it had failed. | ARM confirmation offers "take off once armed"; the disarm is explained in the UI. |
| 7 | E2E | "Already assigned" from assign-task was shown as an error. | Shown as an informational note (`changed: false`). |
| 8 | SITL integration | A 60 s start-up timeout declared a still-booting PX4 crashed and closed its MAVLink links. | 180 s timeout; on timeout the instance is killed before it is reported; start retries if a link closed. |
| 9 | SITL integration | Bench-only spawn fields (e.g. `ground_z`) were sent to PX4 as parameters. | Only names matching PX4's parameter pattern are sent. |
| 10 | SITL integration | The bench lay outside every inclusion geofence, so pre-arm correctly refused it. | The bench registers (and on stop deactivates) its own inclusion zone. |
| 11 | Sprint 5 regression | Stopping a session left ffmpeg processes running: a stopped SITL camera re-created its encoder on the next Gazebo frame, and a new Gazebo screen recording orphaned the previous one. After many benches the leaked encoders loaded the simulator enough that two Sprint 5 Gazebo tests failed (10x-speed equivalence, link-loss failsafe). | A stopped camera never re-creates its encoder (regression test `test_stopped_camera_never_recreates_its_encoder`); a session stop ends its screen recording; only one recording runs at a time. Both Sprint 5 tests pass again on a clean simulator. |
