# Serena + Hermes Agent Guide (V3)
## Deterministic Routing + Single Coding Agent Design

---

# 1. Overview

This setup integrates:

- Serena (MCP Server) → semantic code intelligence (IDE layer)
- Hermes Agent → execution + orchestration

Design goal:

Use Serena ONLY for coding tasks, controlled by deterministic routing.

---

# 2. Agent Roles (CRITICAL)

| Agent       | Role        | Serena |
|------------|------------|--------|
| main       | coding     | YES    |
| localagent | automation | NO     |
| mailagent  | email ops  | NO     |

Only ONE agent uses Serena → avoids state conflict.

---

# 3. Architecture

Mac Mini

Hermes:
- main (coding agent)
- localagent (automation)
- mailagent (email)

Serena MCP:
- main → http://localhost:9121/mcp

Projects:
~/agentic-ai → /workspaces/projects/

---

# 4. Hermes Config (ONLY for main)

~/.hermes/config.yaml (main profile)

mcp_servers:
  serena:
    url: "http://localhost:9121/mcp"
    timeout: 120
    connect_timeout: 10

IMPORTANT:
- DO NOT configure Serena in localagent
- DO NOT configure Serena in mailagent

---

# 5. No Manual Project Activation

REMOVE:

"Activate project at ..."

Reason:
- manual
- error-prone
- unnecessary

Hermes infers project from:
- task context
- file paths

---

# 6. Deterministic Routing

Create:

~/agentic-ai/hermes_extensions/router.py

def route_task(task: str) -> str:
    t = task.lower()

    if any(k in t for k in [
        "refactor", "rename", "symbol",
        "reference", "definition",
        "where is", "code structure"
    ]):
        return "serena"

    if any(k in t for k in [
        "commit", "branch", "merge", "diff"
    ]):
        return "git"

    if any(k in t for k in [
        "run", "execute", "start", "docker", "test"
    ]):
        return "terminal"

    return "default"

---

# 7. Routing Enforcement

main agent MUST:

1. call router before tool usage
2. ONLY use Serena if route == "serena"

---

# 8. Tool Responsibilities

Serena → code intelligence (symbol, references, refactor)  
terminal → execution  
git → version control  
file → simple read/write  

---

# 9. STRICT RULE

Serena is NEVER used for:

- running commands
- file reading
- simple edits
- docker / infra
- debugging runtime

---

# 10. Tool Filtering

mcp_servers:
  serena:
    url: "http://localhost:9121/mcp"
    tools:
      include:
        - find_symbol
        - find_references
        - rename
        - edit

---

# 11. Git-Aware Workflow

Before commit:

1. git diff
2. review changes
3. validate logic
4. commit

---

# 12. Execution Flow

Task: Refactor CoreTax logic

1. router → serena
2. serena → find_symbol / references
3. serena → edit
4. router → terminal → test
5. router → git → diff
6. commit

---

# 13. Failure Modes

| Issue | Cause |
|------|------|
| slow | Serena overused |
| bad edits | routing failure |
| wrong agent uses Serena | config mistake |

---

# 14. Golden Rule

Serena = IDE  
Hermes main = Developer  
Other agents = Operators  

---

# END
