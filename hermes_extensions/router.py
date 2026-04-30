"""Deterministic task-to-tool router for Hermes integration.

Usage:
    from hermes_extensions.router import route_task
    target = route_task("commit the changes")

Returns one of: "git", "terminal", "default"
"""

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
        One of "git", "terminal", or "default".
    """
    t = task.lower()

    if any(k in t for k in _GIT_KEYWORDS):
        return "git"

    if any(k in t for k in _TERMINAL_KEYWORDS):
        return "terminal"

    return "default"
