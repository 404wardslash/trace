from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Severity(str, Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class FindingStatus(str, Enum):
    idea = "idea"
    confirmed = "confirmed"
    needs_proof = "needs-proof"
    reported = "reported"


class CredStatus(str, Enum):
    valid = "valid"
    invalid = "invalid"
    unknown = "unknown"


class TodoStatus(str, Enum):
    open = "open"
    done = "done"


class EngagementStatus(str, Enum):
    in_progress = "in-progress"
    completed = "completed"


class Engagement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    slug: str = Field(index=True, unique=True)
    description: str = ""
    status: EngagementStatus = EngagementStatus.in_progress
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class Target(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    nickname: str = Field(index=True)
    address: str = Field(index=True)
    tags: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class Service(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    target_id: Optional[int] = Field(default=None, foreign_key="target.id", index=True)
    port: int
    protocol: str = "tcp"
    name: str = ""
    product: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class Note(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    target_id: Optional[int] = Field(default=None, foreign_key="target.id", index=True)
    body: str
    tags: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class Finding(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    target_id: Optional[int] = Field(default=None, foreign_key="target.id", index=True)
    title: str = Field(index=True)
    severity: Severity = Severity.info
    status: FindingStatus = FindingStatus.idea
    affected_service: str = ""
    description: str = ""
    steps_to_reproduce: str = ""
    evidence: str = ""
    impact: str = ""
    remediation: str = ""
    tags: str = ""
    linked_command_ids: str = ""
    linked_evidence_ids: str = ""
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class Credential(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    target_id: Optional[int] = Field(default=None, foreign_key="target.id", index=True)
    username: str = ""
    secret: str = ""
    credential_type: str = "password"
    service: str = ""
    source_note: str = ""
    tags: str = ""
    status: CredStatus = CredStatus.unknown
    created_at: datetime = Field(default_factory=now_utc)


class Todo(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    target_id: Optional[int] = Field(default=None, foreign_key="target.id", index=True)
    text: str
    status: TodoStatus = TodoStatus.open
    priority: str = "normal"
    tags: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class Evidence(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    target_id: Optional[int] = Field(default=None, foreign_key="target.id", index=True)
    finding_id: Optional[int] = Field(default=None, foreign_key="finding.id", index=True)
    filename: str
    path: str
    mime_type: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class CommandSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    target_id: Optional[int] = Field(default=None, foreign_key="target.id", index=True)
    kind: str = "shell"
    started_at: datetime = Field(default_factory=now_utc)
    ended_at: Optional[datetime] = None
    cwd: str = ""


class CommandLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[int] = Field(default=None, foreign_key="commandsession.id", index=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    target_id: Optional[int] = Field(default=None, foreign_key="target.id", index=True)
    finding_id: Optional[int] = Field(default=None, foreign_key="finding.id", index=True)
    command: str
    output: str = ""
    exit_code: int = 0
    cwd: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class CustomSnippet(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    category: str = "Custom"
    command: str
    description: str = ""
    tags: str = ""
    created_at: datetime = Field(default_factory=now_utc)


class TimelineEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="engagement.id", index=True)
    target_id: Optional[int] = Field(default=None, foreign_key="target.id", index=True)
    kind: str = Field(index=True)
    title: str
    body: str = ""
    ref_table: str = ""
    ref_id: Optional[int] = None
    created_at: datetime = Field(default_factory=now_utc)
