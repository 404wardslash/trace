from __future__ import annotations

import re
import shutil
from pathlib import Path

from sqlmodel import Session, SQLModel, or_, select

from .config import active_engagement_name, active_target_name, engagement_dir, set_active
from .models import (
    CommandLog,
    CommandSession,
    Credential,
    Engagement,
    Evidence,
    Finding,
    Note,
    Service,
    Target,
    TimelineEvent,
    Todo,
    now_utc,
)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return slug or "engagement"


def add_and_commit(session: Session, obj: SQLModel):
    session.add(obj)
    session.commit()
    session.refresh(obj)
    return obj


def timeline(session: Session, engagement_id: int, kind: str, title: str, body: str = "", target_id: int | None = None, ref_table: str = "", ref_id: int | None = None) -> None:
    add_and_commit(session, TimelineEvent(engagement_id=engagement_id, target_id=target_id, kind=kind, title=title, body=body, ref_table=ref_table, ref_id=ref_id))


def create_engagement(session: Session, name: str, description: str = "") -> Engagement:
    slug = slugify(name)
    existing = session.exec(select(Engagement).where(Engagement.slug == slug)).first()
    if existing:
        return existing
    engagement_dir(slug)
    (Path.home() / "Desktop" / "ctf" / slug).mkdir(parents=True, exist_ok=True)
    eng = add_and_commit(session, Engagement(name=name, slug=slug, description=description))
    set_active(slug)
    timeline(session, eng.id, "engagement", f"Created engagement {name}", ref_table="engagement", ref_id=eng.id)
    return eng


def get_engagement(session: Session, slug_or_name: str | None = None) -> Engagement:
    name = slug_or_name or active_engagement_name()
    if not name:
        raise ValueError("No active engagement. Run: trace init NAME or trace open NAME")
    eng = session.exec(select(Engagement).where(or_(Engagement.slug == name, Engagement.name == name))).first()
    if not eng:
        raise ValueError(f"Engagement not found: {name}")
    return eng


def get_target(session: Session, engagement_id: int, nickname: str | None = None) -> Target | None:
    nick = nickname or active_target_name()
    if not nick:
        return None
    return session.exec(select(Target).where(Target.engagement_id == engagement_id, Target.nickname == nick)).first()


def require_target(session: Session, engagement_id: int, nickname: str | None = None) -> Target:
    target = get_target(session, engagement_id, nickname)
    if not target:
        raise ValueError(f"Target not found: {nickname or active_target_name()}")
    return target


def create_target(session: Session, nickname: str, address: str, tags: str = "", notes: str = "") -> Target:
    eng = get_engagement(session)
    target = add_and_commit(session, Target(engagement_id=eng.id, nickname=nickname, address=address, tags=tags, notes=notes))
    timeline(session, eng.id, "target", f"Added target {nickname}", address, target_id=target.id, ref_table="target", ref_id=target.id)
    return target


def active_target_id(session: Session, engagement_id: int, nickname: str | None = None) -> int | None:
    target = get_target(session, engagement_id, nickname)
    return target.id if target else None


def copy_evidence(session: Session, source: Path, target_name: str | None = None, finding_id: int | None = None, notes: str = "", mime_type: str = "") -> Evidence:
    eng = get_engagement(session)
    target_id = active_target_id(session, eng.id, target_name)
    dest_dir = engagement_dir(eng.slug) / "evidence"
    dest = dest_dir / source.name
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{source.stem}-{counter}{source.suffix}"
        counter += 1
    shutil.copy2(source, dest)
    ev = add_and_commit(session, Evidence(engagement_id=eng.id, target_id=target_id, finding_id=finding_id, filename=dest.name, path=str(dest), mime_type=mime_type, notes=notes))
    timeline(session, eng.id, "evidence", f"Added evidence {dest.name}", notes, target_id=target_id, ref_table="evidence", ref_id=ev.id)
    return ev


def search_all(session: Session, query: str, engagement_id: int | None = None) -> list[tuple[str, int, str, str]]:
    q = f"%{query.lower()}%"
    eng = get_engagement(session) if engagement_id is None else session.get(Engagement, engagement_id)
    eid = eng.id
    results: list[tuple[str, int, str, str]] = []
    specs = [
        ("target", Target, [Target.nickname, Target.address, Target.tags, Target.notes], Target.engagement_id),
        ("service", Service, [Service.name, Service.product, Service.notes, Service.protocol], Service.engagement_id),
        ("note", Note, [Note.body, Note.tags], Note.engagement_id),
        ("finding", Finding, [Finding.title, Finding.description, Finding.evidence, Finding.impact, Finding.remediation, Finding.tags], Finding.engagement_id),
        ("credential", Credential, [Credential.username, Credential.secret, Credential.service, Credential.tags], Credential.engagement_id),
        ("todo", Todo, [Todo.text, Todo.tags, Todo.priority], Todo.engagement_id),
        ("evidence", Evidence, [Evidence.filename, Evidence.notes, Evidence.mime_type], Evidence.engagement_id),
        ("command", CommandLog, [CommandLog.command, CommandLog.output, CommandLog.cwd], CommandLog.engagement_id),
    ]
    for label, model, fields, eid_field in specs:
        stmt = select(model).where(eid_field == eid).where(or_(*[field.ilike(q) for field in fields])).limit(50)
        for row in session.exec(stmt):
            title = getattr(row, "title", None) or getattr(row, "nickname", None) or getattr(row, "filename", None) or getattr(row, "command", None) or getattr(row, "text", None) or getattr(row, "username", None) or getattr(row, "name", "")
            body = getattr(row, "body", None) or getattr(row, "description", None) or getattr(row, "output", None) or getattr(row, "notes", "")
            results.append((label, row.id or 0, str(title), str(body)[:240]))
    return results


def touch(obj):
    if hasattr(obj, "updated_at"):
        obj.updated_at = now_utc()
    return obj
