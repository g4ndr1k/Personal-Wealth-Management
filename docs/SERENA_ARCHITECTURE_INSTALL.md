# Serena Architecture And Installation

Architecture notes and repeatable installation steps for running Serena as a shared MCP server on a different Mac.

## Architecture

Serena runs as one Docker container in Streamable HTTP MCP mode. Claude Code, Codex CLI, and Gemini CLI all connect to the same local endpoint:

```text
Claude Code ┐
Codex CLI   ├─ MCP over HTTP ─ http://localhost:9121/mcp ─ Docker container: serena-mcp
Gemini CLI  ┘
```

The Docker container is managed by a macOS launchd agent:

```text
login
  -> launchd agent: io.serena.mcp
    -> ~/serena/start.sh
      -> wait until Docker Desktop is ready
      -> docker run ghcr.io/oraios/serena:latest
```

The container receives the project through a bind mount:

```text
Host:      ~/agentic-ai
Container: /workspaces/projects
```

Because the repo itself is mounted to `/workspaces/projects`, the Serena project path to activate is:

```text
/workspaces/projects
```

If you instead mount a parent directory such as `~/Code` to `/workspaces/projects`, then activate projects as `/workspaces/projects/<repo-name>`.

## Design Choices

| Choice | Reason |
|---|---|
| Docker image | Avoids managing Serena Python dependencies directly on the host |
| One shared MCP server | Claude, Codex, and Gemini can all reuse the same Serena service |
| `127.0.0.1:9121` binding | Keeps Serena reachable only from the local Mac |
| launchd | Starts Serena automatically on login |
| Docker readiness loop | launchd can start before Docker Desktop is ready |
| `ThrottleInterval` 30 | Prevents fast restart loops |
| Bind mount repo to `/workspaces/projects` | Gives Serena access to the project files inside the container |

## Prerequisites On A New Mac

Install and verify Docker Desktop:

```bash
docker --version
docker ps
```

Docker Desktop should be configured to start on login.

Install or verify the CLIs you want to use:

```bash
command -v claude || true
command -v codex || true
command -v gemini || true
```

Clone or copy this repo to:

```text
~/agentic-ai
```

If you choose another path, update `PROJECTS_DIR` in the startup script and adjust the project activation path accordingly.

## Install Serena Docker Service

Pull the image:

```bash
docker pull ghcr.io/oraios/serena:latest
docker image ls ghcr.io/oraios/serena
```

Create the service directory:

```bash
mkdir -p ~/serena
```

Create `~/serena/start.sh`:

```bash
cat > ~/serena/start.sh <<'EOF'
#!/usr/bin/env bash
# Serena MCP server startup script - managed by launchd.
# Edit PROJECTS_DIR and SERENA_PORT here if your setup changes.

PROJECTS_DIR="$HOME/agentic-ai"
SERENA_PORT=9121
CONTAINER_NAME="serena-mcp"

# Wait for Docker Desktop to be ready before proceeding.
# launchd may start this script before Docker has fully initialized after login.
until docker info >/dev/null 2>&1; do
  sleep 5
done

# Remove any stopped container with the same name before starting.
docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

exec docker run \
  --rm \
  --name "${CONTAINER_NAME}" \
  -p 127.0.0.1:${SERENA_PORT}:${SERENA_PORT} \
  -v "${PROJECTS_DIR}:/workspaces/projects" \
  ghcr.io/oraios/serena:latest \
  serena start-mcp-server \
    --transport streamable-http \
    --host 0.0.0.0 \
    --port "${SERENA_PORT}" \
    --open-web-dashboard false
EOF

chmod +x ~/serena/start.sh
```

Create and install the launchd plist:

```bash
cat > ~/Library/LaunchAgents/io.serena.mcp.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>io.serena.mcp</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$HOME/serena/start.sh</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>30</integer>

    <key>StandardOutPath</key>
    <string>$HOME/serena/serena.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/serena/serena-error.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>$HOME</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
EOF
```

Validate the plist:

```bash
plutil -lint ~/Library/LaunchAgents/io.serena.mcp.plist
grep -E 'serena|HOME' ~/Library/LaunchAgents/io.serena.mcp.plist
```

The paths should contain the real home path, not a literal `$HOME`.

Load and start the service:

```bash
launchctl load ~/Library/LaunchAgents/io.serena.mcp.plist
launchctl start io.serena.mcp
```

Verify:

```bash
launchctl list | grep serena
docker ps --filter name=serena-mcp
```

## Configure Claude Code

Add Serena as a user-scoped MCP server:

```bash
claude mcp add --scope user --transport http serena http://localhost:9121/mcp
```

Verify:

```bash
claude mcp list
```

Expected:

```text
serena: http://localhost:9121/mcp (HTTP) - ✓ Connected
```

## Configure Codex CLI

Edit or create `~/.codex/config.toml` and add:

```toml
[mcp_servers.serena]
url = "http://localhost:9121/mcp"
```

If the file already exists, append the table without replacing existing settings.

Start Codex and verify inside the session:

```text
/mcp
```

## Configure Gemini CLI

Edit or create `~/.gemini/settings.json`.

If the file does not exist, use:

```json
{
  "mcpServers": {
    "serena": {
      "httpUrl": "http://localhost:9121/mcp"
    }
  }
}
```

If the file already exists, merge in the `mcpServers.serena` object without removing existing settings.

Validate JSON:

```bash
python3 -m json.tool ~/.gemini/settings.json >/dev/null && echo ok
```

## First Prompt In Each CLI

Use the same activation prompt in Claude, Codex, or Gemini:

```text
Use Serena. Call initial_instructions, then activate the project at /workspaces/projects and show the current Serena config.
```

For a different mount layout:

```text
Use Serena. Call initial_instructions, then activate the project at /workspaces/projects/<repo-name> and show the current Serena config.
```

## Full Verification Checklist

```bash
docker --version
docker ps --filter name=serena-mcp
launchctl list | grep serena
plutil -lint ~/Library/LaunchAgents/io.serena.mcp.plist
claude mcp list
python3 -m json.tool ~/.gemini/settings.json >/dev/null && echo gemini-json-ok
```

Protocol check:

```bash
curl -i -s -X POST http://localhost:9121/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-03-26",
      "capabilities": {},
      "clientInfo": {
        "name": "manual-verify",
        "version": "1.0"
      }
    }
  }'
```

Healthy response:

```text
HTTP/1.1 200 OK
serverInfo
Serena
```

## Current Mac Reference

This Mac was installed with:

| Item | Value |
|---|---|
| User | `g4ndr1k` |
| Host repo path | `/Users/g4ndr1k/agentic-ai` |
| Startup script | `/Users/g4ndr1k/serena/start.sh` |
| launchd plist | `/Users/g4ndr1k/Library/LaunchAgents/io.serena.mcp.plist` |
| Codex config | `/Users/g4ndr1k/.codex/config.toml` |
| Gemini config | `/Users/g4ndr1k/.gemini/settings.json` |
| Claude config | `/Users/g4ndr1k/.claude.json` |

## Uninstall

Stop and unload launchd:

```bash
launchctl stop io.serena.mcp
launchctl unload ~/Library/LaunchAgents/io.serena.mcp.plist
```

Remove the container if it is still present:

```bash
docker rm -f serena-mcp
```

Remove service files:

```bash
rm -f ~/Library/LaunchAgents/io.serena.mcp.plist
rm -rf ~/serena
```

Remove MCP entries from:

- `~/.claude.json`
- `~/.codex/config.toml`
- `~/.gemini/settings.json`

Do not remove `~/.serena` unless you intentionally want to delete local Serena CLI config and memories.

