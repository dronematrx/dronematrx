import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.payload import VALID_PAYLOAD_STATUS, VALID_PAYLOAD_TYPES


class PayloadBase(BaseModel):
    type: str
    name: str
    status: str = "uncalibrated"
    calibrated_at: datetime | None = None


class PayloadCreate(PayloadBase):
    pass


class PayloadUpdate(BaseModel):
    type: str | None = None
    name: str | None = None
    status: str | None = None
    calibrated_at: datetime | None = None


class PayloadOut(PayloadBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    drone_id: uuid.UUID
    created_at: datetime


assert set(VALID_PAYLOAD_TYPES) and set(VALID_PAYLOAD_STATUS)
