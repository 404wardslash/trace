from __future__ import annotations

import mimetypes
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.table import Table
from datetime import datetime, timezone

from sqlmodel import func as sqlfunc, select

from .config import active_engagement_name, active_target_name, clear_active, engagement_dir, set_active, trace_home
from .crud import add_and_commit, copy_evidence, create_engagement, create_target, get_engagement, get_target, search_all, timeline
from .db import init_db, session_scope
from .export import markdown_report
from .models import CommandLog, CommandSession, Credential, Engagement, Evidence, Finding, Note, Service, Target, TimelineEvent, Todo, now_utc
from .terminal import clean_terminal_output

_EPILOG = """
[bold]Quick reference[/bold]

  [cyan]trace init "Corp Pentest"[/cyan]
  [cyan]trace use web01[/cyan]
  [cyan]trace target add web01 10.10.10.5[/cyan]

  [cyan]trace note add "Found open NFS share"[/cyan]
  [cyan]trace cred add admin Password123[/cyan]
  [cyan]trace todo add "Check SMB signing"[/cyan]
  [cyan]trace finding add "SQLi" --sev critical[/cyan]

  [cyan]trace import nmap scan.xml[/cyan]
  [cyan]trace snippet list --search revshell[/cyan]
  [cyan]trace snippet show rev-bash[/cyan]

  [cyan]trace run "nmap -sV 10.10.10.5"[/cyan]
  [cyan]trace shell[/cyan]
  [cyan]trace web[/cyan]

  [cyan]trace --install-completion[/cyan]
"""

app = typer.Typer(no_args_is_help=False, rich_markup_mode="rich", epilog=_EPILOG)
target_app  = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace target add web01 10.10.10.5\n  trace target add dc01 10.10.10.1 --tags 'windows,dc'\n  trace target list\n  trace use web01")
note_app    = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace note add \"Found open NFS share\"\n  trace note add \"Long note...\" --target web01\n  trace note list\n  trace note list --target web01")
todo_app    = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace todo add \"Check SMB signing\"\n  trace todo add \"Privesc attempt\" --priority high\n  trace todo list\n  trace todo done 3")
finding_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace finding add \"SQL Injection\" --sev critical\n  trace finding add \"Open redirect\" --sev low\n  trace finding list\n  trace finding list --sev high")
cred_app    = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace cred add admin Password123\n  trace cred add administrator --secret aad3...ee --type hash\n  trace cred list")
service_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace service add 80 --name http\n  trace service add 445 --name smb --target web01\n  trace service list")
evidence_app= typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace evidence add screenshot.png\n  trace evidence add proof.txt --target web01\n  trace evidence list")
export_app  = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace export md\n  trace export md --include-creds --include-commands")
import_app  = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace import nmap scan.xml\n  trace import nmap scan.xml --target web01\n  trace import nmap scan.gnmap --create")
snippet_app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich", epilog="[bold]Examples[/bold]\n  trace snippet list\n  trace snippet list --cat \"Reverse Shells\"\n  trace snippet list --search netcat\n  trace snippet show rev-bash\n  trace snippet show listen-nc --lport 9001")
app.add_typer(target_app,   name="target",   help="Manage targets.")
app.add_typer(note_app,     name="note",     help="Log and list notes.")
app.add_typer(todo_app,     name="todo",     help="Manage todo items.")
app.add_typer(finding_app,  name="finding",  help="Log and review findings.")
app.add_typer(cred_app,     name="cred",     help="Log and list credentials.")
app.add_typer(service_app,  name="service",  help="Manage services on targets.")
app.add_typer(evidence_app, name="evidence", help="Attach evidence files.")
app.add_typer(export_app,   name="export",   help="Export engagement data.")
app.add_typer(import_app,   name="import",   help="Import scan results and data.")
app.add_typer(snippet_app,  name="snippet",  help="Browse and copy command snippets.")
console = Console()


def fail(message: str) -> None:
    raise typer.BadParameter(message)


def _age(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (now - dt).total_seconds()
    m = int(secs // 60)
    if m < 60:
        return f"{m}m ago"
    h = int(secs // 3600)
    if h < 24:
        return f"{h}h ago"
    return f"{h // 24}d ago"


def _show_dashboard() -> None:
    from rich.panel import Panel

    eng_name = active_engagement_name()
    tgt_name = active_target_name()

    if not eng_name:
        console.print(Panel(
            "  [dim]No active engagement.[/dim]\n\n"
            "  [cyan]trace init \"Engagement Name\"[/cyan]   create & open\n"
            "  [cyan]trace open <slug>[/cyan]               switch to existing\n"
            "  [cyan]trace list[/cyan]                      list all\n\n"
            "  [dim]Run [cyan]trace --install-completion[/cyan] then restart shell for tab completion.[/dim]",
            title="[bold]trace[/bold]",
            border_style="bright_black",
            padding=(0, 2),
        ))
        return

    try:
        init_db()
        with session_scope() as session:
            eng = session.exec(select(Engagement).where(Engagement.slug == eng_name)).first()
            if not eng:
                console.print(f"[red]Engagement not found:[/red] {eng_name}")
                return

            tgt_obj = get_target(session, eng.id, tgt_name) if tgt_name else None

            tc = session.exec(select(sqlfunc.count(Target.id)).where(Target.engagement_id == eng.id)).one()
            fc = session.exec(select(sqlfunc.count(Finding.id)).where(Finding.engagement_id == eng.id)).one()
            cc = session.exec(select(sqlfunc.count(Credential.id)).where(Credential.engagement_id == eng.id)).one()
            td = session.exec(select(sqlfunc.count(Todo.id)).where(Todo.engagement_id == eng.id, Todo.status == "open")).one()

            events = session.exec(
                select(TimelineEvent)
                .where(TimelineEvent.engagement_id == eng.id)
                .order_by(TimelineEvent.created_at.desc())
                .limit(5)
            ).all()

            lines: list[str] = []

            if tgt_obj:
                lines.append(f"  [dim]target[/dim]  [bold]{tgt_obj.nickname}[/bold]  [dim]({tgt_obj.address})[/dim]")
            else:
                lines.append("  [dim]no target — [/dim][cyan]trace use <nickname>[/cyan]")

            lines.append("")
            lines.append(
                f"  [bold]{tc}[/bold] [dim]targets[/dim]   "
                f"[bold]{fc}[/bold] [dim]findings[/dim]   "
                f"[bold]{cc}[/bold] [dim]creds[/dim]   "
                f"[bold]{td}[/bold] [dim]open todos[/dim]"
            )

            if events:
                lines.append("")
                lines.append("  [dim]recent[/dim]")
                for ev in events:
                    lines.append(f"  [dim]·[/dim] {ev.title[:56]:<56} [dim]{_age(ev.created_at)}[/dim]")

            lines.append("")
            lines.append(
                "  [dim]web[/dim]  [cyan]http://127.0.0.1:8765[/cyan]   "
                "[dim]·  trace --help  for all commands[/dim]"
            )

            console.print(Panel(
                "\n".join(lines),
                title=f"[bold green]●[/bold green]  [bold]{eng.name}[/bold]",
                border_style="bright_black",
                padding=(0, 0),
            ))

    except Exception as exc:
        console.print(f"[red]Dashboard error:[/red] {exc}")


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _show_dashboard()


# ── engagement ────────────────────────────────────────────────────────────────

@app.command()
def init(name: str, description: str = "", status: str = typer.Option("in-progress", "--status", help="Engagement status: in-progress or completed.")):
    """Create and open an engagement."""
    init_db()
    with session_scope() as session:
        eng = create_engagement(session, name, description)
        eng.status = status
        session.add(eng)
        session.commit()
        console.print(f"Active engagement: [bold]{eng.slug}[/bold]")


@app.command()
def open(name: str):
    """Set the active engagement."""
    with session_scope() as session:
        eng = get_engagement(session, name)
        set_active(eng.slug)
        engagement_dir(eng.slug)
        console.print(f"Active engagement: [bold]{eng.slug}[/bold]")


@app.command(name="list")
def list_engagements():
    """List all engagements."""
    with session_scope() as session:
        active = active_engagement_name()
        rows = session.exec(select(Engagement).order_by(Engagement.updated_at.desc(), Engagement.name)).all()
        table = Table("Active", "Name", "Slug", "Status", "Created")
        for row in rows:
            table.add_row("*" if row.slug == active else "", row.name, row.slug, row.status.value, str(row.created_at))
        console.print(table)


@app.command()
def status():
    """Show active engagement and target."""
    eng = active_engagement_name()
    tgt = active_target_name()
    console.print(f"Engagement: [bold]{eng or 'none'}[/bold]")
    console.print(f"Target:     [bold]{tgt or 'none'}[/bold]")


@app.command()
def prompt(zsh: bool = typer.Option(False, "--zsh", help="Print zsh/tmux setup snippet.")):
    """Print a compact status string for shell and tmux prompts."""
    if zsh:
        snippet = r"""
# ── Trace prompt integration ─────────────────────────────────────────────────
# Paste these lines at the END of ~/.zshrc, then run: source ~/.zshrc
#
# Uses a precmd hook so it works regardless of theme/plugin load order.
# Replaces ㉿hostname with your active engagement/target on every render.

autoload -Uz add-zsh-hook
_trace_set_prompt() {
  local _tp
  _tp="$(trace prompt 2>/dev/null)"
  PROMPT="${_TRACE_PROMPT_TPL/㉿%m/㉿${_tp:-$(hostname -s)}}"
}
[[ -z "$_TRACE_PROMPT_TPL" ]] && _TRACE_PROMPT_TPL="$PROMPT"
add-zsh-hook precmd _trace_set_prompt
# ─────────────────────────────────────────────────────────────────────────────
""".strip()
        print(snippet)
        return

    if os.environ.get("TRACE_RECORDING"):
        eng = os.environ.get("TRACE_ENGAGEMENT", "?")
        ip = os.environ.get("TRACE_TARGET_IP", "")
        suffix = f" · {ip}" if ip else ""
        print(f"● {eng}{suffix}")
        return

    eng_name = active_engagement_name()
    if not eng_name:
        raise typer.Exit(1)

    tgt_name = active_target_name()
    tgt_ip = ""
    if tgt_name:
        try:
            with session_scope() as session:
                eng = get_engagement(session, eng_name)
                tgt = get_target(session, eng.id, tgt_name)
                if tgt:
                    tgt_ip = tgt.address
        except Exception:
            pass

    if tgt_ip:
        print(f"{eng_name} · {tgt_ip}")
    elif tgt_name:
        print(f"{eng_name} · {tgt_name}")
    else:
        print(eng_name)


# ── off (deactivate) ─────────────────────────────────────────────────────────

@app.command()
def off():
    """Clear the active engagement and target (prompt goes blank)."""
    clear_active()
    console.print("[bold]Trace deactivated.[/bold] No active engagement or target.")


# ── drop (delete engagement) ─────────────────────────────────────────────────

@app.command()
def drop(slug: str, yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt.")):
    """Permanently delete an engagement and all its data."""
    with session_scope() as session:
        eng = session.exec(select(Engagement).where(Engagement.slug == slug)).first()
        if not eng:
            fail(f"No engagement found with slug: {slug}")
        if not yes:
            typer.confirm(f"Delete engagement '{eng.slug}' and ALL its data? This cannot be undone.", abort=True)
        for model in [TimelineEvent, CommandLog, CommandSession, Evidence, Todo, Credential, Finding, Note, Service, Target]:
            for row in session.exec(select(model).where(model.engagement_id == eng.id)).all():
                session.delete(row)
            session.flush()
        folder = trace_home() / "engagements" / slug
        session.delete(eng)
        session.commit()
        if folder.exists():
            shutil.rmtree(folder)
        if active_engagement_name() == slug:
            remaining = session.exec(select(Engagement).order_by(Engagement.name)).first()
            if remaining:
                set_active(remaining.slug)
            else:
                clear_active()
        console.print(f"Deleted engagement [bold]{slug}[/bold].")


# ── use (set active target) ───────────────────────────────────────────────────

@app.command()
def use(nickname: str):
    """Set the active target (used by default when --target is omitted)."""
    with session_scope() as session:
        eng = get_engagement(session)
        target = get_target(session, eng.id, nickname)
        if not target:
            fail(f"Target not found: {nickname}")
        set_active(target=target.nickname)
        console.print(f"Active target: [bold]{target.nickname}[/bold] ({target.address})")


# ── target ────────────────────────────────────────────────────────────────────

@target_app.command("add")
def target_add(nickname: str, address: str, tags: str = "", notes: str = ""):
    """Add a target to the active engagement."""
    with session_scope() as session:
        target = create_target(session, nickname, address, tags, notes)
        console.print(f"Added target [bold]{target.nickname}[/bold] {target.address}")


@target_app.command("list")
def target_list():
    """List targets in the active engagement."""
    with session_scope() as session:
        eng = get_engagement(session)
        active_tgt = active_target_name()
        rows = session.exec(select(Target).where(Target.engagement_id == eng.id).order_by(Target.nickname)).all()
        table = Table("Active", "Nickname", "Address", "Tags")
        for row in rows:
            table.add_row("*" if row.nickname == active_tgt else "", row.nickname, row.address, row.tags)
        console.print(table)


# ── note ──────────────────────────────────────────────────────────────────────

@note_app.command("add")
def note_add(text: str, target: str | None = typer.Option(None, help="Target nickname (defaults to active target)."), tags: str = ""):
    """Add a note."""
    with session_scope() as session:
        eng = get_engagement(session)
        target_obj = get_target(session, eng.id, target)
        note_obj = add_and_commit(session, Note(engagement_id=eng.id, target_id=target_obj.id if target_obj else None, body=text, tags=tags))
        timeline(session, eng.id, "note", text[:80], text, target_id=note_obj.target_id, ref_table="note", ref_id=note_obj.id)
        tgt_label = f" → target: {target_obj.nickname}" if target_obj else ""
        console.print(f"Added note #{note_obj.id}{tgt_label}")


@note_app.command("list")
def note_list(target: str | None = typer.Option(None, help="Filter by target nickname.")):
    """List notes in the active engagement."""
    with session_scope() as session:
        eng = get_engagement(session)
        rows = list(session.exec(select(Note).where(Note.engagement_id == eng.id).order_by(Note.created_at.desc())).all())
        targets = {t.id: t.nickname for t in session.exec(select(Target).where(Target.engagement_id == eng.id)).all()}
        if target:
            target_obj = get_target(session, eng.id, target)
            if target_obj:
                rows = [n for n in rows if n.target_id == target_obj.id]
        table = Table("ID", "Target", "Tags", "Body")
        for n in rows:
            table.add_row(str(n.id), targets.get(n.target_id, "") if n.target_id else "", n.tags, n.body[:80].replace("\n", " "))
        console.print(table)


# ── todo ──────────────────────────────────────────────────────────────────────

@todo_app.command("add")
def todo_add(text: str, target: str | None = typer.Option(None, help="Target nickname (defaults to active target)."), priority: str = "normal", tags: str = ""):
    """Add a todo item."""
    with session_scope() as session:
        eng = get_engagement(session)
        target_obj = get_target(session, eng.id, target)
        todo_obj = add_and_commit(session, Todo(engagement_id=eng.id, target_id=target_obj.id if target_obj else None, text=text, priority=priority, tags=tags))
        timeline(session, eng.id, "todo", text[:80], text, target_id=todo_obj.target_id, ref_table="todo", ref_id=todo_obj.id)
        tgt_label = f" → target: {target_obj.nickname}" if target_obj else ""
        console.print(f"Added todo #{todo_obj.id}{tgt_label}")


@todo_app.command("list")
def todo_list(status: str = typer.Option("", help="Filter by status: open, done."), target: str | None = typer.Option(None, help="Filter by target nickname.")):
    """List todos in the active engagement."""
    with session_scope() as session:
        eng = get_engagement(session)
        rows = list(session.exec(select(Todo).where(Todo.engagement_id == eng.id).order_by(Todo.status, Todo.priority)).all())
        targets = {t.id: t.nickname for t in session.exec(select(Target).where(Target.engagement_id == eng.id)).all()}
        if status:
            rows = [t for t in rows if t.status.value == status]
        if target:
            target_obj = get_target(session, eng.id, target)
            if target_obj:
                rows = [t for t in rows if t.target_id == target_obj.id]
        table = Table("ID", "Status", "Priority", "Target", "Text", "Tags")
        for t in rows:
            table.add_row(str(t.id), t.status.value, t.priority, targets.get(t.target_id, "") if t.target_id else "", t.text, t.tags)
        console.print(table)


@todo_app.command("done")
def todo_done(todo_id: int):
    """Mark a todo as done."""
    with session_scope() as session:
        eng = get_engagement(session)
        todo = session.exec(select(Todo).where(Todo.id == todo_id, Todo.engagement_id == eng.id)).first()
        if not todo:
            fail(f"Todo #{todo_id} not found")
        todo.status = "done"
        session.add(todo)
        session.commit()
        console.print(f"Marked todo #{todo_id} as done")


# ── finding ───────────────────────────────────────────────────────────────────

@finding_app.command("add")
def finding_add(title: str, severity: str = "info", status: str = "idea", target: str | None = None, service: str = "", tags: str = ""):
    """Add a finding."""
    with session_scope() as session:
        eng = get_engagement(session)
        target_obj = get_target(session, eng.id, target)
        finding = add_and_commit(session, Finding(engagement_id=eng.id, target_id=target_obj.id if target_obj else None, title=title, severity=severity, status=status, affected_service=service, tags=tags))
        timeline(session, eng.id, "finding", title, severity, target_id=finding.target_id, ref_table="finding", ref_id=finding.id)
        tgt_label = f" → target: {target_obj.nickname}" if target_obj else ""
        console.print(f"Added finding #{finding.id}{tgt_label}")


@finding_app.command("list")
def finding_list(severity: str = "", status: str = "", target: str | None = typer.Option(None)):
    """List findings in the active engagement."""
    with session_scope() as session:
        eng = get_engagement(session)
        rows = list(session.exec(select(Finding).where(Finding.engagement_id == eng.id).order_by(Finding.severity, Finding.title)).all())
        targets = {t.id: t.nickname for t in session.exec(select(Target).where(Target.engagement_id == eng.id)).all()}
        if severity:
            rows = [f for f in rows if f.severity.value == severity]
        if status:
            rows = [f for f in rows if f.status.value == status]
        if target:
            target_obj = get_target(session, eng.id, target)
            if target_obj:
                rows = [f for f in rows if f.target_id == target_obj.id]
        table = Table("ID", "Severity", "Status", "Target", "Title", "Service")
        for f in rows:
            table.add_row(str(f.id), f.severity.value, f.status.value, targets.get(f.target_id, "") if f.target_id else "", f.title, f.affected_service)
        console.print(table)


# ── cred ──────────────────────────────────────────────────────────────────────

@cred_app.command("add")
def cred_add(password: str = typer.Option("", "--password", "--secret"), username: str = "", target: str | None = None, service: str = "", type: str = "password", source_note: str = "", tags: str = "", status: str = "unknown"):
    """Add a credential."""
    with session_scope() as session:
        eng = get_engagement(session)
        target_obj = get_target(session, eng.id, target)
        cred = add_and_commit(session, Credential(engagement_id=eng.id, target_id=target_obj.id if target_obj else None, username=username, secret=password, credential_type=type, service=service, source_note=source_note, tags=tags, status=status))
        timeline(session, eng.id, "credential", f"{username} {service}".strip(), source_note, target_id=cred.target_id, ref_table="credential", ref_id=cred.id)
        tgt_label = f" → target: {target_obj.nickname}" if target_obj else ""
        console.print(f"Added credential #{cred.id}{tgt_label}")


@cred_app.command("list")
def cred_list(status: str = "", target: str | None = typer.Option(None)):
    """List credentials in the active engagement."""
    with session_scope() as session:
        eng = get_engagement(session)
        rows = list(session.exec(select(Credential).where(Credential.engagement_id == eng.id).order_by(Credential.created_at.desc())).all())
        targets = {t.id: t.nickname for t in session.exec(select(Target).where(Target.engagement_id == eng.id)).all()}
        if status:
            rows = [c for c in rows if c.status.value == status]
        if target:
            target_obj = get_target(session, eng.id, target)
            if target_obj:
                rows = [c for c in rows if c.target_id == target_obj.id]
        table = Table("ID", "Username", "Secret", "Type", "Service", "Status", "Target")
        for c in rows:
            table.add_row(str(c.id), c.username, c.secret, c.credential_type, c.service, c.status.value, targets.get(c.target_id, "") if c.target_id else "")
        console.print(table)


# ── service ───────────────────────────────────────────────────────────────────

@service_app.command("add")
def service_add(port: int = typer.Option(...), protocol: str = "tcp", name: str = "", target: str | None = None, product: str = "", notes: str = ""):
    """Add a service."""
    with session_scope() as session:
        eng = get_engagement(session)
        target_obj = get_target(session, eng.id, target)
        svc = add_and_commit(session, Service(engagement_id=eng.id, target_id=target_obj.id if target_obj else None, port=port, protocol=protocol, name=name, product=product, notes=notes))
        timeline(session, eng.id, "service", f"{port}/{protocol} {name}".strip(), product, target_id=svc.target_id, ref_table="service", ref_id=svc.id)
        tgt_label = f" → target: {target_obj.nickname}" if target_obj else ""
        console.print(f"Added service #{svc.id}{tgt_label}")


@service_app.command("list")
def service_list(target: str | None = typer.Option(None)):
    """List services in the active engagement."""
    with session_scope() as session:
        eng = get_engagement(session)
        rows = list(session.exec(select(Service).where(Service.engagement_id == eng.id).order_by(Service.port)).all())
        targets = {t.id: t.nickname for t in session.exec(select(Target).where(Target.engagement_id == eng.id)).all()}
        if target:
            target_obj = get_target(session, eng.id, target)
            if target_obj:
                rows = [s for s in rows if s.target_id == target_obj.id]
        table = Table("ID", "Target", "Port", "Protocol", "Name", "Product")
        for s in rows:
            table.add_row(str(s.id), targets.get(s.target_id, "") if s.target_id else "", str(s.port), s.protocol, s.name, s.product)
        console.print(table)


# ── evidence ──────────────────────────────────────────────────────────────────

@evidence_app.command("add")
def evidence_add(path: Path, target: str | None = None, finding: int | None = None, notes: str = ""):
    """Add an evidence file."""
    if not path.exists():
        fail(f"File not found: {path}")
    with session_scope() as session:
        mime = mimetypes.guess_type(path.name)[0] or ""
        ev = copy_evidence(session, path, target, finding, notes, mime)
        console.print(f"Added evidence #{ev.id}: {ev.filename}")


@evidence_app.command("list")
def evidence_list(target: str | None = typer.Option(None)):
    """List evidence files in the active engagement."""
    with session_scope() as session:
        eng = get_engagement(session)
        rows = list(session.exec(select(Evidence).where(Evidence.engagement_id == eng.id).order_by(Evidence.created_at.desc())).all())
        targets = {t.id: t.nickname for t in session.exec(select(Target).where(Target.engagement_id == eng.id)).all()}
        if target:
            target_obj = get_target(session, eng.id, target)
            if target_obj:
                rows = [e for e in rows if e.target_id == target_obj.id]
        table = Table("ID", "Target", "Filename", "MIME", "Notes")
        for e in rows:
            table.add_row(str(e.id), targets.get(e.target_id, "") if e.target_id else "", e.filename, e.mime_type, e.notes[:60])
        console.print(table)


# ── recording ─────────────────────────────────────────────────────────────────

def _recorded_pty(argv: list[str], kind: str, target_nickname: str | None = None) -> int:
    try:
        import pty
    except ImportError:
        console.print("Recorded shells require a POSIX pty. Run this on Kali/Linux.")
        return 2

    with session_scope() as session:
        eng = get_engagement(session)
        target = get_target(session, eng.id, target_nickname)
        sess = add_and_commit(session, CommandSession(engagement_id=eng.id, target_id=target.id if target else None, kind=kind, cwd=os.getcwd()))
        eng_slug = eng.slug
        tgt_nickname = target.nickname if target else ""
        tgt_ip = target.address if target else ""

    command = " ".join(shlex.quote(part) for part in argv)
    output = bytearray()

    def read(fd):
        data = os.read(fd, 1024)
        output.extend(data)
        return data

    # Expose recording state to the child shell so prompts can detect it.
    prev: dict[str, str | None] = {}
    for key, val in [("TRACE_RECORDING", "1"), ("TRACE_ENGAGEMENT", eng_slug), ("TRACE_TARGET", tgt_nickname), ("TRACE_TARGET_IP", tgt_ip)]:
        prev[key] = os.environ.get(key)
        os.environ[key] = val

    try:
        status = pty.spawn(argv, read)
        try:
            exit_code = os.waitstatus_to_exitcode(status)
        except ValueError:
            exit_code = status
    except FileNotFoundError:
        console.print(f"Command not found: {argv[0]}")
        exit_code = 127
    finally:
        for key, old_val in prev.items():
            if old_val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_val

    transcript = clean_terminal_output(output.decode(errors="replace"))
    with session_scope() as session:
        eng = get_engagement(session)
        target = get_target(session, eng.id, target_nickname)
        session_obj = session.get(CommandSession, sess.id)
        log = add_and_commit(session, CommandLog(session_id=sess.id, engagement_id=eng.id, target_id=target.id if target else None, command=command, output=transcript, exit_code=exit_code, cwd=os.getcwd()))
        if session_obj:
            session_obj.ended_at = now_utc()
            session.add(session_obj)
            session.commit()
        timeline(session, eng.id, "command", command, transcript[:500], target_id=log.target_id, ref_table="commandlog", ref_id=log.id)
    return exit_code


@app.command()
def shell(
    shell_path: str = typer.Option("", "--shell", help="Shell binary to use."),
    target: str | None = typer.Option(None, "--target", help="Target nickname to associate with this session (defaults to active target)."),
):
    """Start a recorded interactive shell."""
    selected = shell_path or os.environ.get("SHELL") or shutil.which("bash") or shutil.which("sh") or "sh"
    raise typer.Exit(_recorded_pty([selected], "shell", target))


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def ssh(ctx: typer.Context, destination: str):
    """Start a recorded SSH session using system ssh."""
    argv = ["ssh", destination, *ctx.args]
    raise typer.Exit(_recorded_pty(argv, "ssh"))


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    command: str = typer.Argument(...),
    target: str | None = typer.Option(None),
    finding: int | None = typer.Option(None),
    shell: bool = typer.Option(False, "--shell", help="Run through the system shell."),
):
    """Run one command, print output, and save a command log."""
    extra = list(ctx.args)
    if extra:
        argv = [command, *extra]
        display_command = " ".join(shlex.quote(part) for part in argv)
        popen_args: str | list[str] = argv
        use_shell = False
    else:
        display_command = command
        popen_args = command if shell else shlex.split(command)
        use_shell = shell

    with session_scope() as session:
        eng = get_engagement(session)
        target_obj = get_target(session, eng.id, target)
        sess = add_and_commit(session, CommandSession(engagement_id=eng.id, target_id=target_obj.id if target_obj else None, kind="run", cwd=os.getcwd()))

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    try:
        proc = subprocess.Popen(
            popen_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=use_shell,
            cwd=os.getcwd(),
            bufsize=1,
        )
    except FileNotFoundError:
        output = f"Command not found: {popen_args[0] if isinstance(popen_args, list) else popen_args}"
        console.print(output)
        exit_code = 127
    else:
        assert proc.stdout is not None
        assert proc.stderr is not None
        for line in proc.stdout:
            stdout_chunks.append(line)
            console.print(line, end="")
        stderr = proc.stderr.read()
        if stderr:
            stderr_chunks.append(stderr)
            console.print(stderr, end="", style="red")
        exit_code = proc.wait()
        output = "".join(stdout_chunks)
        if stderr_chunks:
            output = f"{output}\n[stderr]\n{''.join(stderr_chunks)}"

    with session_scope() as session:
        eng = get_engagement(session)
        target_obj = get_target(session, eng.id, target)
        session_obj = session.get(CommandSession, sess.id)
        log = add_and_commit(session, CommandLog(session_id=sess.id, engagement_id=eng.id, target_id=target_obj.id if target_obj else None, finding_id=finding, command=display_command, output=output, exit_code=exit_code, cwd=os.getcwd()))
        if session_obj:
            session_obj.ended_at = now_utc()
            session.add(session_obj)
            session.commit()
        tgt_label = f" target={target_obj.nickname}" if target_obj else ""
        timeline(session, eng.id, "command", display_command, output[:500], target_id=log.target_id, ref_table="commandlog", ref_id=log.id)
        console.print(f"\nSaved command log #{log.id} exit={exit_code}{tgt_label}")
    raise typer.Exit(exit_code)


@app.command()
def search(query: str):
    """Search across all record types in the active engagement."""
    with session_scope() as session:
        results = search_all(session, query)
        table = Table("Type", "ID", "Title", "Context")
        for kind, row_id, title, body in results:
            table.add_row(kind, str(row_id), title, body.replace("\n", " "))
        console.print(table)


@export_app.command("md")
def export_md(include_creds: bool = False, include_commands: bool = False):
    """Export the active engagement as a Markdown report."""
    with session_scope() as session:
        eng = get_engagement(session)
        out = markdown_report(session, eng, include_creds, include_commands)
        console.print(f"Markdown report written: {out}")


# ── import ────────────────────────────────────────────────────────────────────

@import_app.command("nmap")
def import_nmap(
    file: Path = typer.Argument(..., exists=True, help="nmap XML (-oX) or grepable (-oG) output file"),
    target: str = typer.Option("", "--target", "-t", help="Force all hosts into this target nickname"),
    create: bool = typer.Option(False, "--create", help="Auto-create targets for unmatched IPs"),
):
    """Import nmap results and populate services for matching targets."""
    from .nmap_parser import parse_nmap

    try:
        hosts = parse_nmap(file)
    except ValueError as exc:
        console.print(f"[red]Parse error:[/red] {exc}")
        raise typer.Exit(1)

    if not hosts:
        console.print("[yellow]No live hosts found in scan file.[/yellow]")
        raise typer.Exit(1)

    with session_scope() as session:
        eng = get_engagement(session)
        total_services = 0
        total_skipped_hosts = 0

        for host in hosts:
            # Resolve target record
            if target:
                tgt = get_target(session, eng.id, target)
                if not tgt:
                    console.print(f"[red]Target not found:[/red] {target}")
                    raise typer.Exit(1)
            else:
                tgt = session.exec(
                    select(Target).where(Target.engagement_id == eng.id, Target.address == host.address)
                ).first()
                if not tgt:
                    if create:
                        nickname = (host.hostname.split(".")[0] if host.hostname else host.address)
                        tgt = create_target(session, nickname, host.address)
                        console.print(f"  [green]Created[/green] target [bold]{tgt.nickname}[/bold] ({host.address})")
                    else:
                        console.print(f"  [yellow]No target for {host.address}[/yellow] — skipping (use --create to auto-create)")
                        total_skipped_hosts += 1
                        continue

            # Append OS guess to notes
            if host.os_guess:
                os_line = f"OS: {host.os_guess}"
                if os_line not in (tgt.notes or ""):
                    tgt.notes = (tgt.notes + f"\n{os_line}").strip() if tgt.notes else os_line
                    session.add(tgt)

            # Import services (skip duplicates)
            added = 0
            for svc in host.services:
                existing = session.exec(
                    select(Service).where(
                        Service.target_id == tgt.id,
                        Service.port == svc.port,
                        Service.protocol == svc.protocol,
                    )
                ).first()
                if existing:
                    continue
                session.add(Service(
                    engagement_id=eng.id,
                    target_id=tgt.id,
                    port=svc.port,
                    protocol=svc.protocol,
                    name=svc.name,
                    product=svc.product,
                    notes=svc.notes,
                ))
                added += 1
                total_services += 1
            timeline(session, eng.id, "nmap", f"Imported nmap for {tgt.nickname}", f"{added} services from {file.name}", target_id=tgt.id)

            label = f"[bold]{tgt.nickname}[/bold] ({host.address})"
            if host.os_guess:
                label += f"  [dim]{host.os_guess}[/dim]"
            console.print(f"  {label}  →  [green]{added}[/green] services added ({len(host.services) - added} already existed)")

        session.commit()

    console.print(f"\nDone. [bold]{total_services}[/bold] services imported.")
    if total_skipped_hosts:
        console.print(f"[yellow]{total_skipped_hosts} host(s) skipped — no matching target.[/yellow]")


# ── snippets ─────────────────────────────────────────────────────────────────

@snippet_app.command("list")
def snippet_list(
    category: str = typer.Option("", "--cat", "-c", help="Filter by category name (partial match)"),
    query: str = typer.Option("", "--search", "-s", help="Search name, description, and tags"),
):
    """List available pentest command snippets."""
    from .snippets import SNIPPETS

    rows = SNIPPETS
    filtered = bool(category or query)
    if category:
        cat_lower = category.lower()
        rows = [s for s in rows if cat_lower in s["category"].lower()]
    if query:
        q = query.lower()
        rows = [s for s in rows if q in s["name"].lower() or q in s["description"].lower() or q in " ".join(s.get("tags", [])).lower()]
    if filtered:
        table = Table("ID", "Category", "Name", "Command")
        for s in rows:
            table.add_row(s["id"], s["category"], s["name"], s["command"])
    else:
        table = Table("ID", "Category", "Name", "Description")
        for s in rows:
            table.add_row(s["id"], s["category"], s["name"], s["description"])
    console.print(table)


@snippet_app.command("show")
def snippet_show(snippet_id: str, lport: str = typer.Option("4444", "--lport", help="Local listener port")):
    """Show a snippet with LHOST/RHOST substituted from active context."""
    from .snippets import SNIPPETS, detect_lhost

    match = next((s for s in SNIPPETS if s["id"] == snippet_id), None)
    if not match:
        console.print(f"[red]Snippet not found:[/red] {snippet_id}")
        raise typer.Exit(1)

    lhost = detect_lhost()
    rhost = ""
    try:
        eng_name = active_engagement_name()
        tgt_name = active_target_name()
        if eng_name and tgt_name:
            with session_scope() as session:
                eng = get_engagement(session)
                tgt = get_target(session, eng.id, tgt_name)
                if tgt:
                    rhost = tgt.address
    except Exception:
        pass

    cmd = (
        match["command"]
        .replace("{LHOST}", lhost)
        .replace("{RHOST}", rhost or "{RHOST}")
        .replace("{LPORT}", lport)
        .replace("{TARGET}", rhost or "{TARGET}")
    )
    console.print(f"[bold]{match['name']}[/bold]  [dim]{match['category']}[/dim]")
    console.print(f"[dim]{match['description']}[/dim]\n")
    console.print(cmd, markup=False)


@app.command()
def web(host: str = "127.0.0.1", port: int = 8765):
    """Launch the local web UI."""
    init_db()
    console.print(f"Trace web UI: http://{host}:{port}")
    uvicorn.run("traceapp.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
