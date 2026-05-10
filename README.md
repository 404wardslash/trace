# Trace

Trace is a local pentest / CTF notebook.

Use it to quickly save:

- targets
- notes
- findings
- creds
- todos
- services
- screenshots/evidence
- command output

It runs on your machine only. No cloud. No background spying on your terminal. It only logs commands you run through `trace run`, `trace shell`, or `trace ssh`.

## Install

On Kali, put the Trace folder wherever you want, then run:

```bash
cd Trace
bash install.sh
trace --help
```

That should install the `trace` command so it works in new terminals too.

If `trace` does not work after opening a new terminal, run:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
trace --help
```

If you use zsh:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
trace --help
```

## Start a Box / Lab

```bash
trace init oscp-lab
trace list
trace target add web01 10.10.10.5 --tags linux,http
trace target add dc01 10.10.10.10 --tags windows,ad
trace use web01
```

Open the web UI:

```bash
trace web
```

Then go to:

```text
http://127.0.0.1:8765
```

You can also create, rename, delete, and switch engagements from the web UI on the `Engagements` page.

Command logs have their own `Commands` page in the web UI.

## Quick Commands

Add a note:

```bash
trace note "Found /admin endpoint"
```

Add a todo:

```bash
trace todo "Try default creds" --priority high
```

Add a service:

```bash
trace service add --port 80 --protocol tcp --name http --product "nginx"
```

Add a finding:

```bash
trace finding add "Anonymous FTP exposes backup.zip" --severity high --service ftp
```

Add creds:

```bash
trace cred add --username admin --password admin --service http --status unknown
```

Add evidence:

```bash
trace evidence add screenshot.png --notes "Admin login page"
```

Search everything:

```bash
trace search jenkins
trace search 10.10.10.5
trace search password
```

## Command Logging

Run one command and save the output:

```bash
trace run "whoami"
trace run nmap -sV 10.10.10.5
```

Attach it to a specific target:

```bash
trace run --target dc01 "nmap -sV 10.10.10.10"
```

Attach it to a finding:

```bash
trace run --finding 3 "curl -i http://10.10.10.5/admin"
```

Start a recorded shell:

```bash
trace shell
```

Start a recorded SSH session:

```bash
trace ssh user@10.10.10.5
```

Trace does not record random terminals. Only stuff started through Trace gets logged.

## Export a Report

```bash
trace export md
```

Include creds and command logs:

```bash
trace export md --include-creds --include-commands
```

Exports go here:

```text
~/.trace/engagements/<engagement>/exports/
```

## Where Data Lives

Default location:

```text
~/.trace/
  trace.db
  config.json
  engagements/
    oscp-lab/
      evidence/
      exports/
```

Use a different folder if you want:

```bash
export TRACE_HOME=/mnt/shared/trace-data
trace web
```

## Backup

Back up everything:

```bash
tar -czf trace-backup.tgz ~/.trace
```

Restore by putting it back at `~/.trace`.

## Moving It to Another Machine

From your dev machine:

```bash
make bundle
```

Copy this file to Kali:

```text
dist/trace-operator-src.tgz
```

On Kali:

```bash
mkdir Trace
tar -xzf trace-operator-src.tgz -C Trace
cd Trace
bash install.sh
trace --help
```

## Installer Options

Normal install:

```bash
bash install.sh
```

Install with test tools:

```bash
bash install.sh --dev
```

Install into `.venv` instead of pipx:

```bash
bash install.sh --venv
```

Skip apt stuff:

```bash
bash install.sh --no-apt
```

## Dev Stuff

Run tests:

```bash
bash install.sh --dev
pytest
```

Clean local junk:

```bash
make clean
```

## Notes

- No encryption in v1.
- Keep `~/.trace` private.
- Do not commit `.trace/`, reports, or evidence files.
- The web UI binds to localhost by default.
