class CommandHandler:
    def __init__(self, state):
        self.state = state
        # Restore persisted flags
        self.paused = state.get_bool_flag("paused")
        self.quiet = state.get_bool_flag("quiet")
        self.scan_requested = False

    def handle(self, text: str) -> str:
        cmd = text.strip().lower()
        if cmd.startswith("agent:"):
            cmd = cmd[len("agent:"):].strip()

        if cmd == "help":
            return (
                "Commands: status, summary, test, scan, pause, resume, "
                "quiet on, quiet off, health, last 5"
            )
        if cmd == "status":
            return f"Agent running. paused={self.paused}, quiet={self.quiet}"
        if cmd == "summary":
            return self._format_recent()
        if cmd == "test":
            return "Test OK - agent is responding"
        if cmd == "scan":
            self.scan_requested = True
            return "Manual scan scheduled for next cycle"
        if cmd == "pause":
            self.paused = True
            self.state.set_bool_flag("paused", True)
            return "Agent paused - no more scans until resumed"
        if cmd == "resume":
            self.paused = False
            self.state.set_bool_flag("paused", False)
            return "Agent resumed"
        if cmd == "quiet on":
            self.quiet = True
            self.state.set_bool_flag("quiet", True)
            return "Quiet mode: alerts suppressed"
        if cmd == "quiet off":
            self.quiet = False
            self.state.set_bool_flag("quiet", False)
            return "Quiet mode off: alerts active"
        if cmd == "health":
            return "Agent healthy and running"
        if cmd == "last 5":
            return self._format_recent()

        return "Unknown command. Send 'agent: help' for options"

    def _format_recent(self) -> str:
        rows = self.state.recent_alerts(5)
        if not rows:
            return "No recent alerts"
        lines = []
        for r in rows:
            status = "ok" if r[3] else "fail"
            lines.append(f"{r[0]} | {r[1]} | {status}")
        return "\n".join(lines)