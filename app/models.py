from sqlmodel import SQLModel, Field
from typing import Optional, Dict
from datetime import datetime
import uuid

class Job(SQLModel, table=True):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    status: str = "queued"  # queued|running|completed|failed
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    input_text: Optional[str] = None
    output_text: Optional[str] = None
    error: Optional[str] = None
