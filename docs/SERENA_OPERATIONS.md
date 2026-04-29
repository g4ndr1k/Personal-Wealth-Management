# Serena Operations

Day-to-day runbook for the shared Serena MCP server used by Claude Code, Codex CLI, and Gemini CLI on this Mac.

## Current Setup

| Item | Value |
|---|---|
| MCP endpoint | `http://localhost:9121/mcp` |
| Docker container | `serena-mcp` |
| Docker image | `ghcr.io/oraios/serena:latest` |
| launchd label | `io.serena.mcp` |
| Startup script | `~/serena/start.sh` |
| launchd plist | `~/Library/LaunchAgents/io.serena.mcp.plist` |
| Host project path | `~/agentic-ai` |
| Container project path | `/workspaces/projects` |

The service is managed by launchd and should start automatically when you log in. It waits for Docker Desktop to become available, removes any old `serena-mcp` container, then starts a fresh Docker container bound to localhost only.

## Start, Stop, Restart

```bash
# Start
launchctl start io.serena.mcp

# Stop
launchctl stop io.serena.mcp

# Restart
launchctl stop io.serena.mcp
launchctl start io.serena.mcp
```

If the service has been unloaded:

```bash
launchctl load ~/Library/LaunchAgents/io.serena.mcp.plist
```

To disable automatic startup:

```bash
launchctl unload ~/Library/LaunchAgents/io.serena.mcp.plist
```

## Verify Serena Is Running

Check launchd:

```bash
launchctl list | grep serena
```

Healthy output looks like this:

```text
31827   0   io.serena.mcp
```

The first column is the process ID. The second column is the last exit code. `0` is healthy.

Check Docker:

```bash
docker ps --filter name=serena-mcp
```

Expected port mapping:

```text
127.0.0.1:9121->9121/tcp
```

Check MCP protocol health:

```bash
curl -i http://localhost:9121/mcp
```

A plain `curl` may return `406 Not Acceptable` with a message saying the client must accept `text/event-stream`. That is normal for this Serena build. Real MCP clients send the correct headers.

For a stronger protocol check:

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

Healthy output includes `HTTP/1.1 200 OK`, `serverInfo`, and `name: Serena`.

## Logs

```bash
# launchd stdout
tail -f ~/serena/serena.log

# launchd stderr and Serena logs
tail -f ~/serena/serena-error.log

# Docker container logs
docker logs -f serena-mcp
```

Serena also writes logs inside the container under `/workspaces/serena/config/logs/`.

## Using Serena In Claude Code

Verify the MCP server is configured and connected:

```bash
claude mcp list
```

Expected line:

```text
serena: http://localhost:9121/mcp (HTTP) - ✓ Connected
```

Start Claude normally:

```bash
claude
```

Use this as the first prompt:

```text
Use Serena. Call initial_instructions, then activate the project at /workspaces/projects and show the current Serena config.
```

## Using Serena In Codex CLI

Start Codex normally:

```bash
codex
```

Inside Codex, check MCP status:

```text
/mcp
```

Use this as the first prompt:

```text
Use Serena. Call initial_instructions, then activate the project at /workspaces/projects and show the current Serena config.
```

## Using Serena In Gemini CLI

Validate the Gemini settings file:

```bash
python3 -m json.tool ~/.gemini/settings.json >/dev/null && echo ok
```

Start Gemini normally:

```bash
gemini
```

Use this as the first prompt:

```text
Use Serena. Call initial_instructions, then activate the project at /workspaces/projects and show the current Serena config.
```

## Confirm Serena Is Active In A Session

The client should be able to call Serena tools such as:

- `initial_instructions`
- `activate_project`
- `get_current_config`
- `list_dir`
- `get_symbols_overview`
- `find_symbol`

A good confirmation prompt is:

```text
Use Serena to show the current active project and list the top-level files.
```

For this Docker setup, the active project path should be:

```text
/workspaces/projects
```

Do not use `/workspaces/projects/agentic-ai` unless the Docker mount is changed to mount the parent directory instead of the repo directory.

## Work In A Different Directory

You do not need to restart the Mac to work in another directory.

Edit `~/serena/start.sh` and change `PROJECTS_DIR`:

```bash
PROJECTS_DIR="$HOME/agentic-ai"
```

Then restart the launchd service:

```bash
launchctl stop io.serena.mcp
launchctl start io.serena.mcp
```

launchd will recreate the `serena-mcp` Docker container with the new bind mount.

Do not normally run `~/serena/start.sh` by hand. launchd should own the service lifecycle.

If you mount a repo directly:

```bash
PROJECTS_DIR="$HOME/my-other-repo"
```

Then activate this path inside Claude, Codex, or Gemini:

```text
/workspaces/projects
```

If you mount a parent directory:

```bash
PROJECTS_DIR="$HOME"
```

Then activate the repo path under the mounted parent:

```text
/workspaces/projects/my-other-repo
```

After switching directories, use:

```text
Use Serena. Call initial_instructions, then activate the project at /workspaces/projects and show the current Serena config.
```

Adjust the activation path if you mounted a parent directory.

## Update Serena

```bash
docker pull ghcr.io/oraios/serena:latest
launchctl stop io.serena.mcp
launchctl start io.serena.mcp
```

Verify afterward:

```bash
docker ps --filter name=serena-mcp
claude mcp list
```

## Troubleshooting

### `Connection refused`

Check Docker and launchd:

```bash
docker ps --filter name=serena-mcp
launchctl list | grep serena
tail -n 100 ~/serena/serena-error.log
```

Docker Desktop may not be running. Open Docker Desktop and restart the service.

### launchd PID Is Missing Or Keeps Restarting

Check the plist and logs:

```bash
plutil -lint ~/Library/LaunchAgents/io.serena.mcp.plist
tail -n 100 ~/serena/serena-error.log
```

The startup script waits for Docker with `docker info`, so startup may take a little while immediately after login.

### Port 9121 Is Already In Use

Find the process:

```bash
lsof -nP -iTCP:9121 -sTCP:LISTEN
```

If another Serena container is running:

```bash
docker rm -f serena-mcp
launchctl start io.serena.mcp
```

### Claude Shows Serena Disconnected

```bash
claude mcp list
docker ps --filter name=serena-mcp
```

If the container is healthy, restart Claude. If large projects time out, set a longer MCP timeout in your shell profile:

```bash
export MCP_TIMEOUT=60000
```

### Codex Does Not Show Serena

Confirm `~/.codex/config.toml` contains:

```toml
[mcp_servers.serena]
url = "http://localhost:9121/mcp"
```

Restart Codex and run `/mcp`.

### Gemini Does Not Show Serena

Confirm `~/.gemini/settings.json` contains:

```json
{
  "mcpServers": {
    "serena": {
      "httpUrl": "http://localhost:9121/mcp"
    }
  }
}
```

Restart Gemini.

### What About `~/.serena`?

`~/.serena` belongs to a local Serena CLI install and is not used by the Docker service unless explicitly mounted. Keeping it is harmless. For this Docker setup, only `~/agentic-ai` is mounted into the container.

If you want to disable the local config without deleting it:

```bash
mv ~/.serena ~/.serena.backup
```
