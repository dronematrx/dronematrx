import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MaintenanceRecordBase(BaseModel):
    service_date: datetime
    category: str = "routine"
    technician: str
    notes: str = ""
    flight_hours_at_service: float = 0
    battery_cycles_at_service: int = 0


class MaintenanceRecordCreate(MaintenanceRecordBase):
    pass


class MaintenanceRecordOut(MaintenanceRecordBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    drone_id: uuid.UUID
    created_at: datetime
