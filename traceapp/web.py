from __future__ import annotations

from contextlib import asynccontextmanager
import mimetypes
from pathlib import Path
import shutil
import tempfile

import markdown as md
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, func, select

from .config import active_engagement_name, active_target_name, engagement_dir, set_active, trace_home
from .crud import add_and_commit, copy_evidence, create_engagement, get_engagement, get_target, search_all, slugify, timeline, touch
from .db import init_db, session_dependency, session_scope
from .export import markdown_report
from .models import CommandLog, CommandSession, Credential, CustomSnippet, Engagement, Evidence, Finding, Note, Service, Target, TimelineEvent, Todo
from .terminal import clean_terminal_output


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Trace", lifespan=lifespan)
base = Path(__file__).parent
templates = Jinja2Templates(directory=str(base / "templates"))
app.mount("/static", StaticFiles(directory=str(base / "static")), name="static")


def render_markdown(value: str) -> str:
    return md.markdown(value or "", extensions=["fenced_code", "tables"])


def format_dt(value) -> str:
    if not value:
        return ""
    try:
        return value.strftime("%d %b, %H:%M").lstrip("0")
    except Exception:
        return str(value)


templates.env.filters["markdown"] = render_markdown
templates.env.filters["terminal"] = clean_terminal_output
templates.env.filters["dt"] = format_dt


def page(request: Request, name: str, context: dict, session: Session | None = None):
    context.setdefault("request", request)
    if session is not None:
        context.setdefault("engagements", all_engagements(session))
    else:
        with session_scope() as sidebar_session:
            context.setdefault("engagements", all_engagements(sidebar_session))
    return templates.TemplateResponse(request=request, name=name, context=context)


def current(session: Session) -> Engagement | None:
    name = active_engagement_name()
    if not name:
        return None
    return session.exec(select(Engagement).where(Engagement.slug == name)).first()


def active_or_redirect(session: Session) -> Engagement:
    eng = current(session)
    if not eng:
        eng = create_engagement(session, "default")
        set_active(eng.slug)
    return eng


def all_engagements(session: Session) -> list[Engagement]:
    return session.exec(select(Engagement).order_by(Engagement.name)).all()


def owned(session: Session, model, row_id: int, engagement_id: int):
    row = session.get(model, row_id)
    if not row or row.engagement_id != engagement_id:
        raise HTTPException(404)
    return row


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    stats = {
        "targets": session.exec(select(func.count(Target.id)).where(Target.engagement_id == eng.id)).one(),
        "open_todos": session.exec(select(func.count(Todo.id)).where(Todo.engagement_id == eng.id, Todo.status == "open")).one(),
        "findings": session.exec(select(func.count(Finding.id)).where(Finding.engagement_id == eng.id)).one(),
        "creds": session.exec(select(func.count(Credential.id)).where(Credential.engagement_id == eng.id)).one(),
    }
    findings = session.exec(select(Finding).where(Finding.engagement_id == eng.id)).all()
    sev_counts = {sev: len([f for f in findings if f.severity.value == sev]) for sev in ["critical", "high", "medium", "low", "info"]}
    events = session.exec(select(TimelineEvent).where(TimelineEvent.engagement_id == eng.id).order_by(TimelineEvent.created_at.desc()).limit(25)).all()
    targets = session.exec(select(Target).where(Target.engagement_id == eng.id).order_by(Target.nickname)).all()
    return page(request, "dashboard.html", {"eng": eng, "engagements": all_engagements(session), "stats": stats, "sev_counts": sev_counts, "events": events, "targets": targets})


@app.get("/engagements", response_class=HTMLResponse)
def engagements(request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    rows = all_engagements(session)
    counts = {
        row.id: {
            "targets": session.exec(select(func.count(Target.id)).where(Target.engagement_id == row.id)).one(),
            "findings": session.exec(select(func.count(Finding.id)).where(Finding.engagement_id == row.id)).one(),
            "todos": session.exec(select(func.count(Todo.id)).where(Todo.engagement_id == row.id, Todo.status == "open")).one(),
        }
        for row in rows
    }
    return page(request, "engagements.html", {"eng": eng, "engagements": rows, "counts": counts})


@app.post("/engagements")
def add_engagement(name: str = Form(...), description: str = Form(""), status: str = Form("in-progress"), session: Session = Depends(session_dependency)):
    eng = create_engagement(session, name, description)
    eng.status = status
    session.add(eng)
    session.commit()
    set_active(eng.slug)
    return RedirectResponse("/engagements", status_code=303)


@app.post("/engagements/open")
def open_engagement(slug: str = Form(...), next: str = Form("/"), session: Session = Depends(session_dependency)):
    eng = get_engagement(session, slug)
    set_active(eng.slug)
    engagement_dir(eng.slug)
    return RedirectResponse(next or "/", status_code=303)


@app.post("/engagements/{engagement_id}")
def update_engagement(engagement_id: int, name: str = Form(...), description: str = Form(""), status: str = Form("in-progress"), session: Session = Depends(session_dependency)):
    eng = session.get(Engagement, engagement_id)
    if not eng:
        raise HTTPException(404)
    new_slug = slugify(name)
    existing = session.exec(select(Engagement).where(Engagement.slug == new_slug, Engagement.id != engagement_id)).first()
    if existing:
        raise HTTPException(400, "An engagement with that name already exists.")
    old_slug = eng.slug
    old_dir = trace_home() / "engagements" / old_slug
    new_dir = trace_home() / "engagements" / new_slug
    if old_slug != new_slug and old_dir.exists():
        new_dir.parent.mkdir(parents=True, exist_ok=True)
        if new_dir.exists():
            raise HTTPException(400, "Target engagement folder already exists.")
        shutil.move(str(old_dir), str(new_dir))
    elif old_slug != new_slug:
        engagement_dir(new_slug)

    eng.name = name
    eng.slug = new_slug
    eng.description = description
    eng.status = status
    session.add(touch(eng))
    session.commit()
    if active_engagement_name() == old_slug:
        set_active(new_slug)
    return RedirectResponse("/engagements", status_code=303)


@app.post("/engagements/{engagement_id}/delete")
def delete_engagement(engagement_id: int, confirm: str = Form(""), session: Session = Depends(session_dependency)):
    eng = session.get(Engagement, engagement_id)
    if not eng:
        raise HTTPException(404)
    if confirm != eng.slug:
        raise HTTPException(400, "Confirmation did not match engagement slug.")

    for model in [TimelineEvent, CommandLog, CommandSession, Evidence, Todo, Credential, Finding, Note, Service, Target]:
        for row in session.exec(select(model).where(model.engagement_id == eng.id)).all():
            session.delete(row)
        session.flush()
    slug = eng.slug
    session.delete(eng)
    session.commit()

    folder = trace_home() / "engagements" / slug
    if folder.exists():
        shutil.rmtree(folder)

    remaining = session.exec(select(Engagement).order_by(Engagement.name)).first()
    if remaining:
        set_active(remaining.slug)
    else:
        replacement = create_engagement(session, "default")
        set_active(replacement.slug)
    return RedirectResponse("/engagements", status_code=303)


@app.get("/targets", response_class=HTMLResponse)
def targets(request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    rows = session.exec(select(Target).where(Target.engagement_id == eng.id).order_by(Target.nickname)).all()
    return page(request, "targets.html", {"eng": eng, "targets": rows})


@app.post("/targets")
def add_target(nickname: str = Form(...), address: str = Form(...), tags: str = Form(""), notes: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    target = add_and_commit(session, Target(engagement_id=eng.id, nickname=nickname, address=address, tags=tags, notes=notes))
    timeline(session, eng.id, "target", f"Added target {nickname}", address, target_id=target.id, ref_table="target", ref_id=target.id)
    return RedirectResponse("/targets", status_code=303)


@app.get("/targets/{target_id}", response_class=HTMLResponse)
def target_detail(target_id: int, request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    target = session.get(Target, target_id)
    if not target or target.engagement_id != eng.id:
        raise HTTPException(404)
    ctx = {
        "request": request,
        "eng": eng,
        "target": target,
        "targets": session.exec(select(Target).where(Target.engagement_id == eng.id).order_by(Target.nickname)).all(),
        "services": session.exec(select(Service).where(Service.target_id == target.id)).all(),
        "notes": session.exec(select(Note).where(Note.target_id == target.id).order_by(Note.created_at.desc())).all(),
        "findings": session.exec(select(Finding).where(Finding.target_id == target.id)).all(),
        "creds": session.exec(select(Credential).where(Credential.target_id == target.id)).all(),
        "todos": session.exec(select(Todo).where(Todo.target_id == target.id)).all(),
        "evidence": session.exec(select(Evidence).where(Evidence.target_id == target.id)).all(),
        "commands": session.exec(select(CommandLog).where(CommandLog.target_id == target.id).order_by(CommandLog.created_at.desc()).limit(25)).all(),
    }
    return page(request, "target_detail.html", ctx)


@app.post("/targets/{target_id}")
def update_target(target_id: int, nickname: str = Form(...), address: str = Form(...), tags: str = Form(""), notes: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    target = owned(session, Target, target_id, eng.id)
    target.nickname = nickname
    target.address = address
    target.tags = tags
    target.notes = notes
    session.add(target)
    session.commit()
    timeline(session, eng.id, "target", f"Updated target {nickname}", address, target_id=target.id, ref_table="target", ref_id=target.id)
    return RedirectResponse(f"/targets/{target.id}", status_code=303)


@app.post("/targets/{target_id}/delete")
def delete_target(target_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    target = owned(session, Target, target_id, eng.id)
    for model in [Service, Note, Finding, Credential, Todo, Evidence, CommandLog, CommandSession, TimelineEvent]:
        rows = session.exec(select(model).where(model.target_id == target.id)).all()
        for row in rows:
            row.target_id = None
            session.add(row)
    title = target.nickname
    session.delete(target)
    session.commit()
    timeline(session, eng.id, "target", f"Deleted target {title}", "")
    return RedirectResponse("/targets", status_code=303)


@app.post("/quick-note")
def quick_note(body: str = Form(...), target_id: int | None = Form(None), tags: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    note = add_and_commit(session, Note(engagement_id=eng.id, target_id=target_id, body=body, tags=tags))
    timeline(session, eng.id, "note", body[:80], body, target_id=target_id, ref_table="note", ref_id=note.id)
    return RedirectResponse("/notes", status_code=303)


@app.post("/notes/{note_id}")
def update_note(note_id: int, body: str = Form(...), target_id: int | None = Form(None), tags: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    note = owned(session, Note, note_id, eng.id)
    note.body = body
    note.target_id = target_id
    note.tags = tags
    session.add(note)
    session.commit()
    timeline(session, eng.id, "note", f"Updated note #{note.id}", body[:300], target_id=target_id, ref_table="note", ref_id=note.id)
    return RedirectResponse("/notes", status_code=303)


@app.post("/notes/{note_id}/delete")
def delete_note(note_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    note = owned(session, Note, note_id, eng.id)
    session.delete(note)
    session.commit()
    timeline(session, eng.id, "note", f"Deleted note #{note_id}", "")
    return RedirectResponse("/notes", status_code=303)


@app.get("/notes", response_class=HTMLResponse)
def notes(request: Request, q: str = "", session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    stmt = select(Note).where(Note.engagement_id == eng.id).order_by(Note.created_at.desc())
    rows = session.exec(stmt).all()
    if q:
        rows = [n for n in rows if q.lower() in (n.body + n.tags).lower()]
    return page(request, "notes.html", {"eng": eng, "notes": rows, "targets": session.exec(select(Target).where(Target.engagement_id == eng.id)).all(), "q": q})


@app.get("/findings", response_class=HTMLResponse)
def findings(request: Request, severity: str = "", status: str = "", session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    rows = session.exec(select(Finding).where(Finding.engagement_id == eng.id).order_by(Finding.severity, Finding.title)).all()
    if severity:
        rows = [f for f in rows if f.severity.value == severity]
    if status:
        rows = [f for f in rows if f.status.value == status]
    from .finding_templates import FINDING_TEMPLATES
    return page(request, "findings.html", {"eng": eng, "findings": rows, "targets": session.exec(select(Target).where(Target.engagement_id == eng.id)).all(), "severity": severity, "status": status, "finding_templates": FINDING_TEMPLATES})


@app.post("/findings")
def add_finding(title: str = Form(...), severity: str = Form("info"), status: str = Form("idea"), target_id: int | None = Form(None), affected_service: str = Form(""), description: str = Form(""), steps_to_reproduce: str = Form(""), evidence: str = Form(""), impact: str = Form(""), remediation: str = Form(""), tags: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    finding = add_and_commit(session, Finding(engagement_id=eng.id, target_id=target_id, title=title, severity=severity, status=status, affected_service=affected_service, description=description, steps_to_reproduce=steps_to_reproduce, evidence=evidence, impact=impact, remediation=remediation, tags=tags))
    timeline(session, eng.id, "finding", title, description, target_id=target_id, ref_table="finding", ref_id=finding.id)
    return RedirectResponse("/findings", status_code=303)


@app.get("/findings/{finding_id}", response_class=HTMLResponse)
def finding_edit(finding_id: int, request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    finding = session.get(Finding, finding_id)
    if not finding or finding.engagement_id != eng.id:
        raise HTTPException(404)
    return page(request, "finding_edit.html", {"eng": eng, "finding": finding, "targets": session.exec(select(Target).where(Target.engagement_id == eng.id)).all(), "evidence": session.exec(select(Evidence).where(Evidence.finding_id == finding.id).order_by(Evidence.created_at.desc())).all(), "commands": session.exec(select(CommandLog).where(CommandLog.engagement_id == eng.id).order_by(CommandLog.created_at.desc()).limit(100)).all()})


@app.post("/findings/{finding_id}")
def update_finding(finding_id: int, title: str = Form(...), severity: str = Form("info"), status: str = Form("idea"), target_id: int | None = Form(None), affected_service: str = Form(""), description: str = Form(""), steps_to_reproduce: str = Form(""), evidence: str = Form(""), impact: str = Form(""), remediation: str = Form(""), tags: str = Form(""), linked_command_ids: str = Form(""), linked_evidence_ids: str = Form(""), session: Session = Depends(session_dependency)):
    finding = session.get(Finding, finding_id)
    if not finding:
        raise HTTPException(404)
    for key, value in locals().items():
        if key in {"title", "severity", "status", "target_id", "affected_service", "description", "steps_to_reproduce", "evidence", "impact", "remediation", "tags", "linked_command_ids", "linked_evidence_ids"}:
            setattr(finding, key, value)
    session.add(touch(finding))
    session.commit()
    return RedirectResponse(f"/findings/{finding_id}", status_code=303)


@app.post("/findings/{finding_id}/delete")
def delete_finding(finding_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    finding = owned(session, Finding, finding_id, eng.id)
    for ev in session.exec(select(Evidence).where(Evidence.finding_id == finding.id)).all():
        ev.finding_id = None
        session.add(ev)
    for log in session.exec(select(CommandLog).where(CommandLog.finding_id == finding.id)).all():
        log.finding_id = None
        session.add(log)
    title = finding.title
    session.delete(finding)
    session.commit()
    timeline(session, eng.id, "finding", f"Deleted finding {title}", "")
    return RedirectResponse("/findings", status_code=303)


@app.post("/findings/{finding_id}/evidence")
async def upload_finding_evidence(
    finding_id: int,
    upload: UploadFile = File(...),
    notes: str = Form(""),
    session: Session = Depends(session_dependency),
):
    eng = active_or_redirect(session)
    finding = owned(session, Finding, finding_id, eng.id)
    dest_dir = engagement_dir(eng.slug) / "evidence"
    dest = dest_dir / upload.filename
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{Path(upload.filename).stem}-{counter}{Path(upload.filename).suffix}"
        counter += 1
    dest.write_bytes(await upload.read())
    ev = add_and_commit(session, Evidence(
        engagement_id=eng.id,
        target_id=finding.target_id,
        finding_id=finding_id,
        filename=dest.name,
        path=str(dest),
        mime_type=upload.content_type or mimetypes.guess_type(dest.name)[0] or "",
        notes=notes,
    ))
    timeline(session, eng.id, "evidence", f"Added evidence {dest.name}", notes, target_id=finding.target_id, ref_table="evidence", ref_id=ev.id)
    return RedirectResponse(f"/findings/{finding_id}", status_code=303)


@app.post("/findings/{finding_id}/evidence/{evidence_id}/delete")
def delete_finding_evidence(finding_id: int, evidence_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    ev = owned(session, Evidence, evidence_id, eng.id)
    path = Path(ev.path)
    filename = ev.filename
    session.delete(ev)
    session.commit()
    if path.exists() and path.is_file():
        path.unlink()
    timeline(session, eng.id, "evidence", f"Deleted evidence {filename}", "")
    return RedirectResponse(f"/findings/{finding_id}", status_code=303)


@app.get("/credentials", response_class=HTMLResponse)
def credentials(request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    rows = session.exec(select(Credential).where(Credential.engagement_id == eng.id).order_by(Credential.created_at.desc())).all()
    return page(request, "credentials.html", {"eng": eng, "creds": rows, "targets": session.exec(select(Target).where(Target.engagement_id == eng.id)).all()})


@app.post("/credentials")
def add_credential(username: str = Form(""), secret: str = Form(""), credential_type: str = Form("password"), service: str = Form(""), status: str = Form("unknown"), target_id: int | None = Form(None), tags: str = Form(""), source_note: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    cred = add_and_commit(session, Credential(engagement_id=eng.id, target_id=target_id, username=username, secret=secret, credential_type=credential_type, service=service, status=status, tags=tags, source_note=source_note))
    timeline(session, eng.id, "credential", f"{username} {service}".strip(), source_note, target_id=target_id, ref_table="credential", ref_id=cred.id)
    return RedirectResponse("/credentials", status_code=303)


@app.post("/credentials/{cred_id}")
def update_credential(cred_id: int, username: str = Form(""), secret: str = Form(""), credential_type: str = Form("password"), service: str = Form(""), status: str = Form("unknown"), target_id: int | None = Form(None), tags: str = Form(""), source_note: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    cred = owned(session, Credential, cred_id, eng.id)
    cred.username = username
    cred.secret = secret
    cred.credential_type = credential_type
    cred.service = service
    cred.status = status
    cred.target_id = target_id
    cred.tags = tags
    cred.source_note = source_note
    session.add(cred)
    session.commit()
    timeline(session, eng.id, "credential", f"Updated credential #{cred.id}", f"{username} {service}".strip(), target_id=target_id, ref_table="credential", ref_id=cred.id)
    return RedirectResponse("/credentials", status_code=303)


@app.post("/credentials/{cred_id}/delete")
def delete_credential(cred_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    cred = owned(session, Credential, cred_id, eng.id)
    session.delete(cred)
    session.commit()
    timeline(session, eng.id, "credential", f"Deleted credential #{cred_id}", "")
    return RedirectResponse("/credentials", status_code=303)


@app.get("/todos", response_class=HTMLResponse)
def todos(request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    rows = session.exec(select(Todo).where(Todo.engagement_id == eng.id).order_by(Todo.status, Todo.priority)).all()
    return page(request, "todos.html", {"eng": eng, "todos": rows, "targets": session.exec(select(Target).where(Target.engagement_id == eng.id)).all()})


@app.post("/todos")
def add_todo(text: str = Form(...), target_id: int | None = Form(None), priority: str = Form("normal"), tags: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    todo = add_and_commit(session, Todo(engagement_id=eng.id, target_id=target_id, text=text, priority=priority, tags=tags))
    timeline(session, eng.id, "todo", text, "", target_id=target_id, ref_table="todo", ref_id=todo.id)
    return RedirectResponse("/todos", status_code=303)


@app.post("/todos/{todo_id}")
def update_todo(todo_id: int, text: str = Form(...), target_id: int | None = Form(None), status: str = Form("open"), priority: str = Form("normal"), tags: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    todo = owned(session, Todo, todo_id, eng.id)
    todo.text = text
    todo.target_id = target_id
    todo.status = status
    todo.priority = priority
    todo.tags = tags
    session.add(todo)
    session.commit()
    timeline(session, eng.id, "todo", f"Updated todo #{todo.id}", text, target_id=target_id, ref_table="todo", ref_id=todo.id)
    return RedirectResponse("/todos", status_code=303)


@app.post("/todos/{todo_id}/toggle")
def toggle_todo(todo_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    todo = owned(session, Todo, todo_id, eng.id)
    todo.status = "done" if todo.status.value == "open" else "open"
    session.add(todo)
    session.commit()
    return RedirectResponse("/todos", status_code=303)


@app.post("/todos/{todo_id}/delete")
def delete_todo(todo_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    todo = owned(session, Todo, todo_id, eng.id)
    session.delete(todo)
    session.commit()
    timeline(session, eng.id, "todo", f"Deleted todo #{todo_id}", "")
    return RedirectResponse("/todos", status_code=303)


@app.post("/services")
def add_service_web(port: int = Form(...), protocol: str = Form("tcp"), name: str = Form(""), target_id: int | None = Form(None), product: str = Form(""), notes: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    svc = add_and_commit(session, Service(engagement_id=eng.id, target_id=target_id, port=port, protocol=protocol, name=name, product=product, notes=notes))
    timeline(session, eng.id, "service", f"{port}/{protocol} {name}".strip(), product, target_id=target_id, ref_table="service", ref_id=svc.id)
    return RedirectResponse(f"/targets/{target_id}" if target_id else "/targets", status_code=303)


@app.post("/services/{service_id}")
def update_service(service_id: int, port: int = Form(...), protocol: str = Form("tcp"), name: str = Form(""), target_id: int | None = Form(None), product: str = Form(""), notes: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    svc = owned(session, Service, service_id, eng.id)
    svc.port = port
    svc.protocol = protocol
    svc.name = name
    svc.target_id = target_id
    svc.product = product
    svc.notes = notes
    session.add(svc)
    session.commit()
    timeline(session, eng.id, "service", f"Updated service #{svc.id}", f"{port}/{protocol} {name}", target_id=target_id, ref_table="service", ref_id=svc.id)
    return RedirectResponse(f"/targets/{target_id}" if target_id else "/targets", status_code=303)


@app.post("/services/{service_id}/delete")
def delete_service(service_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    svc = owned(session, Service, service_id, eng.id)
    target_id = svc.target_id
    session.delete(svc)
    session.commit()
    timeline(session, eng.id, "service", f"Deleted service #{service_id}", "")
    return RedirectResponse(f"/targets/{target_id}" if target_id else "/targets", status_code=303)


@app.get("/evidence", response_class=HTMLResponse)
def evidence(request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    rows = session.exec(select(Evidence).where(Evidence.engagement_id == eng.id).order_by(Evidence.created_at.desc())).all()
    return page(request, "evidence.html", {"eng": eng, "evidence": rows, "targets": session.exec(select(Target).where(Target.engagement_id == eng.id)).all(), "findings": session.exec(select(Finding).where(Finding.engagement_id == eng.id)).all()})


@app.post("/evidence")
async def upload_evidence(upload: UploadFile = File(...), target_id: int | None = Form(None), finding_id: int | None = Form(None), notes: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    dest_dir = engagement_dir(eng.slug) / "evidence"
    dest = dest_dir / upload.filename
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{Path(upload.filename).stem}-{counter}{Path(upload.filename).suffix}"
        counter += 1
    dest.write_bytes(await upload.read())
    ev = add_and_commit(session, Evidence(engagement_id=eng.id, target_id=target_id, finding_id=finding_id, filename=dest.name, path=str(dest), mime_type=upload.content_type or mimetypes.guess_type(dest.name)[0] or "", notes=notes))
    timeline(session, eng.id, "evidence", f"Added evidence {dest.name}", notes, target_id=target_id, ref_table="evidence", ref_id=ev.id)
    return RedirectResponse("/evidence", status_code=303)


@app.post("/evidence/{evidence_id}")
def update_evidence(evidence_id: int, target_id: int | None = Form(None), finding_id: int | None = Form(None), notes: str = Form(""), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    ev = owned(session, Evidence, evidence_id, eng.id)
    ev.target_id = target_id
    ev.finding_id = finding_id
    ev.notes = notes
    session.add(ev)
    session.commit()
    timeline(session, eng.id, "evidence", f"Updated evidence {ev.filename}", notes, target_id=target_id, ref_table="evidence", ref_id=ev.id)
    return RedirectResponse("/evidence", status_code=303)


@app.post("/evidence/{evidence_id}/delete")
def delete_evidence(evidence_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    ev = owned(session, Evidence, evidence_id, eng.id)
    path = Path(ev.path)
    filename = ev.filename
    session.delete(ev)
    session.commit()
    if path.exists() and path.is_file():
        path.unlink()
    timeline(session, eng.id, "evidence", f"Deleted evidence {filename}", "")
    return RedirectResponse("/evidence", status_code=303)


@app.get("/evidence/{evidence_id}/file")
def evidence_file(evidence_id: int, session: Session = Depends(session_dependency)):
    ev = session.get(Evidence, evidence_id)
    if not ev or not Path(ev.path).exists():
        raise HTTPException(404)
    return FileResponse(ev.path, media_type=ev.mime_type or None, filename=ev.filename)


@app.get("/commands", response_class=HTMLResponse)
def commands(request: Request, q: str = "", target_id: str = "", exit_code: str = "", session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    targets = session.exec(select(Target).where(Target.engagement_id == eng.id).order_by(Target.nickname)).all()
    stmt = select(CommandLog).where(CommandLog.engagement_id == eng.id).order_by(CommandLog.created_at.desc())
    rows = session.exec(stmt).all()
    selected_target_id = int(target_id) if target_id.isdigit() else None
    if selected_target_id:
        rows = [row for row in rows if row.target_id == selected_target_id]
    if exit_code != "":
        try:
            code = int(exit_code)
            rows = [row for row in rows if row.exit_code == code]
        except ValueError:
            rows = []
    if q:
        needle = q.lower()
        rows = [row for row in rows if needle in (row.command + row.output + row.cwd).lower()]
    target_by_id = {target.id: target for target in targets}
    return page(request, "commands.html", {"eng": eng, "commands": rows, "targets": targets, "target_by_id": target_by_id, "q": q, "target_id": selected_target_id, "exit_code": exit_code})


@app.post("/commands/{command_id}/delete")
def delete_command(command_id: int, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    command = owned(session, CommandLog, command_id, eng.id)
    session.delete(command)
    session.commit()
    timeline(session, eng.id, "command", f"Deleted command log #{command_id}", "")
    return RedirectResponse("/commands", status_code=303)


@app.get("/timeline", response_class=HTMLResponse)
def timeline_page(request: Request, q: str = "", session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    rows = session.exec(select(TimelineEvent).where(TimelineEvent.engagement_id == eng.id).order_by(TimelineEvent.created_at.desc()).limit(250)).all()
    if q:
        rows = [r for r in rows if q.lower() in (r.title + r.body + r.kind).lower()]
    return page(request, "timeline.html", {"eng": eng, "events": rows, "q": q})


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "", session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    results = search_all(session, q, eng.id) if q else []
    return page(request, "search.html", {"eng": eng, "q": q, "results": results})


@app.get("/import/nmap", response_class=HTMLResponse)
def import_nmap_page(request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    targets = session.exec(select(Target).where(Target.engagement_id == eng.id).order_by(Target.nickname)).all()
    return page(request, "import_nmap.html", {"eng": eng, "targets": targets, "result": None})


@app.post("/import/nmap", response_class=HTMLResponse)
async def import_nmap(
    request: Request,
    upload: UploadFile = File(...),
    target_id: int | None = Form(None),
    create_targets: bool = Form(False),
    session: Session = Depends(session_dependency),
):
    from .nmap_parser import parse_nmap

    eng = active_or_redirect(session)
    targets = session.exec(select(Target).where(Target.engagement_id == eng.id).order_by(Target.nickname)).all()

    raw = await upload.read()
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=upload.filename or ".xml") as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        hosts = parse_nmap(tmp_path)
        tmp_path.unlink(missing_ok=True)
    except Exception as exc:
        return page(request, "import_nmap.html", {"eng": eng, "targets": targets, "result": {"error": str(exc)}})

    rows = []
    for host in hosts:
        if target_id:
            tgt = session.get(Target, target_id)
            if not tgt or tgt.engagement_id != eng.id:
                tgt = None
        else:
            tgt = session.exec(
                select(Target).where(Target.engagement_id == eng.id, Target.address == host.address)
            ).first()
            if not tgt and create_targets:
                nickname = host.hostname.split(".")[0] if host.hostname else host.address
                tgt = add_and_commit(session, Target(engagement_id=eng.id, nickname=nickname, address=host.address))

        if not tgt:
            rows.append({"host": host, "target": None, "added": 0, "skipped": 0})
            continue

        if host.os_guess:
            os_line = f"OS: {host.os_guess}"
            if os_line not in (tgt.notes or ""):
                tgt.notes = (tgt.notes + f"\n{os_line}").strip() if tgt.notes else os_line
                session.add(tgt)

        added = skipped = 0
        for svc in host.services:
            existing = session.exec(
                select(Service).where(
                    Service.target_id == tgt.id,
                    Service.port == svc.port,
                    Service.protocol == svc.protocol,
                )
            ).first()
            if existing:
                skipped += 1
                continue
            session.add(Service(
                engagement_id=eng.id, target_id=tgt.id,
                port=svc.port, protocol=svc.protocol,
                name=svc.name, product=svc.product, notes=svc.notes,
            ))
            added += 1

        timeline(session, eng.id, "nmap", f"Imported nmap for {tgt.nickname}",
                 f"{added} services from {upload.filename}", target_id=tgt.id)
        rows.append({"host": host, "target": tgt, "added": added, "skipped": skipped})

    session.commit()
    result = {"rows": rows, "filename": upload.filename}
    return page(request, "import_nmap.html", {"eng": eng, "targets": targets, "result": result})


@app.get("/snippets", response_class=HTMLResponse)
def snippets_page(request: Request, session: Session = Depends(session_dependency)):
    from .snippets import CATEGORIES, SNIPPETS, detect_lhost

    eng = current(session)
    lhost = detect_lhost()
    rhost = ""
    if eng:
        tgt_name = active_target_name()
        if tgt_name:
            tgt = session.exec(select(Target).where(Target.engagement_id == eng.id, Target.nickname == tgt_name)).first()
            if tgt:
                rhost = tgt.address
    custom = session.exec(select(CustomSnippet).order_by(CustomSnippet.created_at)).all()
    return page(request, "snippets.html", {"eng": eng, "lhost": lhost, "rhost": rhost, "categories": CATEGORIES, "snippets": SNIPPETS, "custom_snippets": custom})


@app.post("/snippets/custom")
def add_custom_snippet(name: str = Form(...), category: str = Form("Custom"), command: str = Form(...), description: str = Form(""), tags: str = Form(""), session: Session = Depends(session_dependency)):
    add_and_commit(session, CustomSnippet(name=name, category=category, command=command, description=description, tags=tags))
    return RedirectResponse("/snippets", status_code=303)


@app.post("/snippets/custom/{snip_id}/delete")
def delete_custom_snippet(snip_id: int, session: Session = Depends(session_dependency)):
    snip = session.get(CustomSnippet, snip_id)
    if not snip:
        raise HTTPException(404)
    session.delete(snip)
    session.commit()
    return RedirectResponse("/snippets", status_code=303)


@app.get("/report", response_class=HTMLResponse)
def report_page(request: Request, session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    findings = session.exec(select(Finding).where(Finding.engagement_id == eng.id)).all()
    targets = session.exec(select(Target).where(Target.engagement_id == eng.id)).all()
    sev_counts = {s: sum(1 for f in findings if f.severity.value == s) for s in ["critical", "high", "medium", "low", "info"]}
    return page(request, "report.html", {"eng": eng, "sev_counts": sev_counts, "finding_count": len(findings), "target_count": len(targets)})


@app.get("/report/export")
def report_export(
    request: Request,
    assessor: str = "",
    client: str = "",
    classification: str = "CONFIDENTIAL",
    include_creds: bool = False,
    include_commands: bool = False,
    session: Session = Depends(session_dependency),
):
    import base64
    from datetime import date
    from fastapi.responses import Response

    SEV_ORDER = ["critical", "high", "medium", "low", "info"]
    eng = active_or_redirect(session)
    targets = session.exec(select(Target).where(Target.engagement_id == eng.id).order_by(Target.nickname)).all()
    target_by_id = {t.id: t for t in targets}

    services_all = session.exec(select(Service).where(Service.engagement_id == eng.id)).all()
    services_by_target: dict[int, list] = {}
    for svc in services_all:
        if svc.target_id:
            services_by_target.setdefault(svc.target_id, []).append(svc)
    for tid in services_by_target:
        services_by_target[tid].sort(key=lambda s: s.port)

    findings_raw = session.exec(select(Finding).where(Finding.engagement_id == eng.id)).all()
    findings = sorted(findings_raw, key=lambda f: SEV_ORDER.index(f.severity.value))
    sev_counts = {s: sum(1 for f in findings if f.severity.value == s) for s in SEV_ORDER}

    evidence_all = session.exec(select(Evidence).where(Evidence.engagement_id == eng.id)).all()
    evidence_by_finding: dict[int, list] = {}
    evidence_images: dict[int, str] = {}
    for ev in evidence_all:
        if ev.finding_id:
            evidence_by_finding.setdefault(ev.finding_id, []).append(ev)
        if ev.mime_type and ev.mime_type.startswith("image/"):
            p = Path(ev.path)
            if p.exists():
                b64 = base64.b64encode(p.read_bytes()).decode()
                evidence_images[ev.id] = f"data:{ev.mime_type};base64,{b64}"

    creds = session.exec(select(Credential).where(Credential.engagement_id == eng.id)).all() if include_creds else []
    commands = session.exec(select(CommandLog).where(CommandLog.engagement_id == eng.id).order_by(CommandLog.created_at)).all() if include_commands else []

    template = templates.env.get_template("report_output.html")
    html = template.render(
        request=request,
        eng=eng,
        assessor=assessor or "Security Assessor",
        client=client or eng.name,
        classification=classification,
        report_date=date.today().strftime("%B %d, %Y"),
        targets=targets,
        target_by_id=target_by_id,
        services_by_target=services_by_target,
        findings=findings,
        sev_counts=sev_counts,
        evidence_by_finding=evidence_by_finding,
        evidence_images=evidence_images,
        include_creds=include_creds,
        creds=creds,
        include_commands=include_commands,
        commands=commands,
    )
    filename = f"{eng.slug}-pentest-report.html"
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/targets/{target_id}/import-nmap")
async def target_import_nmap(
    target_id: int,
    upload: UploadFile = File(...),
    session: Session = Depends(session_dependency),
):
    from .nmap_parser import parse_nmap

    eng = active_or_redirect(session)
    tgt = owned(session, Target, target_id, eng.id)

    raw = await upload.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xml") as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        hosts = parse_nmap(tmp_path)
    except Exception:
        hosts = []
    tmp_path.unlink(missing_ok=True)

    added = 0
    for host in hosts:
        if host.os_guess:
            os_line = f"OS: {host.os_guess}"
            if os_line not in (tgt.notes or ""):
                tgt.notes = (tgt.notes + f"\n{os_line}").strip() if tgt.notes else os_line
                session.add(tgt)
        for svc in host.services:
            exists = session.exec(
                select(Service).where(
                    Service.target_id == tgt.id,
                    Service.port == svc.port,
                    Service.protocol == svc.protocol,
                )
            ).first()
            if not exists:
                session.add(Service(
                    engagement_id=eng.id, target_id=tgt.id,
                    port=svc.port, protocol=svc.protocol,
                    name=svc.name, product=svc.product, notes=svc.notes,
                ))
                added += 1

    timeline(session, eng.id, "nmap", f"Imported nmap for {tgt.nickname}",
             f"{added} services added", target_id=tgt.id)
    session.commit()
    return RedirectResponse(f"/targets/{target_id}", status_code=303)


@app.post("/export/md")
def web_export_md(include_creds: bool = Form(False), include_commands: bool = Form(False), session: Session = Depends(session_dependency)):
    eng = active_or_redirect(session)
    out = markdown_report(session, eng, include_creds, include_commands)
    return FileResponse(out, media_type="text/markdown", filename=out.name)
