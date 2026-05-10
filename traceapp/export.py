from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from .config import engagement_dir
from .models import Credential, Engagement, Evidence, Finding, Service, Target, CommandLog
from .terminal import clean_terminal_output


SEV_ORDER = ["critical", "high", "medium", "low", "info"]


def markdown_report(session: Session, engagement: Engagement, include_creds: bool = False, include_commands: bool = False) -> Path:
    targets = session.exec(select(Target).where(Target.engagement_id == engagement.id).order_by(Target.nickname)).all()
    findings = session.exec(select(Finding).where(Finding.engagement_id == engagement.id)).all()
    services = session.exec(select(Service).where(Service.engagement_id == engagement.id)).all()
    evidence = session.exec(select(Evidence).where(Evidence.engagement_id == engagement.id)).all()
    target_by_id = {t.id: t for t in targets}
    services_by_target: dict[int, list[Service]] = {}
    for svc in services:
        if svc.target_id:
            services_by_target.setdefault(svc.target_id, []).append(svc)
    evidence_by_finding: dict[int, list[Evidence]] = {}
    for ev in evidence:
        if ev.finding_id:
            evidence_by_finding.setdefault(ev.finding_id, []).append(ev)

    lines = [f"# {engagement.name}", "", "## Scope", ""]
    for target in targets:
        lines.append(f"- **{target.nickname}**: `{target.address}`" + (f" ({target.tags})" if target.tags else ""))
        for svc in services_by_target.get(target.id, []):
            label = f"{svc.port}/{svc.protocol} {svc.name}".strip()
            product = f" - {svc.product}" if svc.product else ""
            lines.append(f"  - {label}{product}")

    lines += ["", "## Findings", ""]
    for severity in SEV_ORDER:
        group = [f for f in findings if f.severity.value == severity]
        if not group:
            continue
        lines += [f"### {severity.title()}", ""]
        for finding in group:
            target = target_by_id.get(finding.target_id) if finding.target_id else None
            lines += [
                f"#### {finding.title}",
                "",
                f"- Severity: {finding.severity.value}",
                f"- Status: {finding.status.value}",
                f"- Affected target: {target.nickname if target else 'Unassigned'}",
                f"- Affected service: {finding.affected_service or 'N/A'}",
                "",
                "##### Description",
                finding.description or "N/A",
                "",
                "##### Steps to Reproduce",
                finding.steps_to_reproduce or "N/A",
                "",
                "##### Evidence",
                finding.evidence or "N/A",
            ]
            for ev in evidence_by_finding.get(finding.id, []):
                lines.append(f"- `{ev.filename}`: {ev.notes}")
            lines += ["", "##### Impact", finding.impact or "N/A", "", "##### Remediation", finding.remediation or "N/A", ""]

    if include_creds:
        lines += ["", "## Credentials Summary", ""]
        creds = session.exec(select(Credential).where(Credential.engagement_id == engagement.id)).all()
        for cred in creds:
            target = target_by_id.get(cred.target_id) if cred.target_id else None
            lines.append(f"- `{cred.username}` / `{cred.secret}` on {target.nickname if target else 'unassigned'} {cred.service} ({cred.status.value})")

    if include_commands:
        lines += ["", "## Command Log Appendix", ""]
        commands = session.exec(select(CommandLog).where(CommandLog.engagement_id == engagement.id).order_by(CommandLog.created_at)).all()
        for log in commands:
            lines += [f"### `{log.command}`", "", f"- Exit code: {log.exit_code}", f"- CWD: `{log.cwd}`", "", "```text", clean_terminal_output(log.output)[-4000:], "```", ""]

    out = engagement_dir(engagement.slug) / "exports" / f"{engagement.slug}-report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
