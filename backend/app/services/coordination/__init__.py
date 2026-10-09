"""Multi-drone task coordination (CBBA today; pluggable for future strategies)."""
from app.services.coordination.base import (
    CoordinationAlgorithm,
    available_algorithms,
    get_algorithm,
    register_algorithm,
)
from app.services.coordination.cbba import CBBAEngine
from app.services.coordination.communication import CommunicationModel, FullyConnected, RangeLimited
from app.services.coordination.models import (
    Agent,
    AgentPlan,
    AllocationParams,
    AllocationProblem,
    AllocationResult,
    InvalidProblemError,
    PathStep,
    Task,
)
from app.services.coordination.utility import DefaultUtility, UtilityModel

__all__ = [
    "Agent",
    "AgentPlan",
    "AllocationParams",
    "AllocationProblem",
    "AllocationResult",
    "CBBAEngine",
    "CommunicationModel",
    "CoordinationAlgorithm",
    "DefaultUtility",
    "FullyConnected",
    "InvalidProblemError",
    "PathStep",
    "RangeLimited",
    "Task",
    "UtilityModel",
    "available_algorithms",
    "get_algorithm",
    "register_algorithm",
]
