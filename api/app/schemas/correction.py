import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.clip import ActionType


class CorrectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clip_id: uuid.UUID
    user_id: uuid.UUID
    original_action: ActionType
    corrected_label_1: str
    corrected_label_2: str | None
    original_confidence: float
    start_time: float
    end_time: float
    created_at: datetime
