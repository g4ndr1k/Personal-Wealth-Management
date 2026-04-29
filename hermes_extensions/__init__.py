"""Deterministic task-to-tool router for Serena + Hermes integration.

Usage:
    from hermes_extensions.router import route_task
    target = route_task("refactor the CoreTax handler")

Returns one of: "serena", "git", "terminal", "default"
"""

# Keywords that signal semantic code intelligence tasks → Serena
_SERENA_KEYWORDS = [
    "refactor", "rename", "symbol",
    "reference", "references", "definition",
    "where is", "code structure", "find symbol",
    "find_referencing_symbols", "get_symbols_overview",
    "replace_symbol_body", "insert_after_symbol", "insert_before_symbol",
]

# Keywords that signal version control → git
_GIT_KEYWORDS = [
    "commit", "branch", "merge", "diff",
    "checkout", "stash", "rebase", "pull request",
    "pr ", "git log", "git status",
]

# Keywords that signal execution / infra → terminal
_TERMINAL_KEYWORDS = [
    "run", "execute", "start", "docker", "test",
    "build", "deploy", "install", "pip", "npm",
    "curl", "ssh", "restart", "logs", "ps",
]


def route_task(task: str) -> str:
    """Route a task description to the appropriate tool target.

    Args:
        task: Natural language description of the task.

    Returns:
        One of "serena", "git", "terminal", or "default".
    """
    t = task.lower()

    if any(k in t for k in _SERENA_KEYWORDS):
        return "serena"

    if any(k in t for k in _GIT_KEYWORDS):
        return "git"

    if any(k in t for k in _TERMINAL_KEYWORDS):
        return "terminal"

    return "default"
