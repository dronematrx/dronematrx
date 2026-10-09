"""Mission events are pushed over the existing /ws/telemetry socket and
scoped to the mission's organization."""
import queue
import threading

from tests.conftest import _make_user
from tests.planning_support import fleet, headers_for, run_wizard  # noqa: F401


def _listen(ws, out: queue.Queue, count: int) -> None:
    try:
        for _ in range(count):
            out.put(ws.receive_json())
    except Exception as exc:  # socket closed at teardown
        out.put(exc)


def _token(client, user) -> str:
    return headers_for(client, user)["Authorization"].split(" ", 1)[1]


def test_state_change_and_plan_events_broadcast(client, operator_user, fleet):
    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet, drones=4)

    received: queue.Queue = queue.Queue()
    with client.websocket_connect(f"/ws/telemetry?token={_token(client, operator_user)}") as ws:
        listener = threading.Thread(target=_listen, args=(ws, received, 3), daemon=True)
        listener.start()
        client.post(f"/missions/{mission_id}/cbba/allocate", headers=headers)
        client.post(f"/missions/{mission_id}/validate", headers=headers)
        client.post(f"/missions/{mission_id}/transition", headers=headers, json={"target_state": "aborted"})
        listener.join(timeout=5)

    events = [received.get_nowait() for _ in range(received.qsize())]
    assert all(isinstance(e, dict) and e["type"] == "mission" and e["mission_id"] == mission_id for e in events), events
    assert [e["event"] for e in events] == ["mission.plan_updated", "mission.validated", "mission.state_changed"]
    assert events[2]["data"] == {"previous": "validating", "status": "aborted"}


def test_events_do_not_leak_to_other_organizations(client, db, operator_user, fleet):
    from app.models import Organization

    other_org = Organization(name="Elsewhere", slug="elsewhere-org")
    db.add(other_org)
    db.commit()
    outsider = _make_user(db, other_org, "OPERATOR")

    headers = headers_for(client, operator_user)
    mission_id = run_wizard(client, headers, fleet, drones=2)

    received: queue.Queue = queue.Queue()
    with client.websocket_connect(f"/ws/telemetry?token={_token(client, outsider)}") as ws:
        listener = threading.Thread(target=_listen, args=(ws, received, 1), daemon=True)
        listener.start()
        client.post(f"/missions/{mission_id}/transition", headers=headers, json={"target_state": "aborted"})
        listener.join(timeout=1.5)
        assert received.empty(), "outsider received another organization's mission event"
