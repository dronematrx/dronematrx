"""Import every model so SQLAlchemy's mapper registry and Alembic autogenerate see them all."""
from app.models.organization import Organization  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.drone import Drone  # noqa: F401
from app.models.payload import Payload  # noqa: F401
from app.models.maintenance_record import MaintenanceRecord  # noqa: F401
from app.models.mission import Mission  # noqa: F401
from app.models.geofence import Geofence  # noqa: F401
from app.models.task_bundle import TaskBundle  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.telemetry import DroneTelemetry  # noqa: F401
from app.models.mission_planning import MissionPlan, MissionStateHistory, MissionTask, TaskAssignment  # noqa: F401
from app.models.simulation import (  # noqa: F401
    SimulationDroneInstance,
    SimulationEvent,
    SimulationReport,
    SimulationReportComparison,
    SimulationSession,
)

__all__ = [
    "Organization",
    "User",
    "Drone",
    "Payload",
    "MaintenanceRecord",
    "Mission",
    "Geofence",
    "TaskBundle",
    "AuditLog",
    "MissionTask",
    "MissionPlan",
    "TaskAssignment",
    "MissionStateHistory",
    "DroneTelemetry",
    "SimulationSession",
    "SimulationDroneInstance",
    "SimulationEvent",
    "SimulationReport",
    "SimulationReportComparison",
]
