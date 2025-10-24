from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional
import uuid

class Job(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    input_text: str
    output_text: Optional[str] = None
    status: str = "queued"
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
