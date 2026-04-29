import json
import sys
import urllib.error
from pathlib import Path

from agent.app import net_guard


class _Response:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _patch_network(monkeypatch, *, outbound=True, imap=True, nas=False):
    def fake_tcp(host, port, timeout=net_guard._TCP_TIMEOUT):
        if port == 53:
            return outbound
        if (host, port) == net_guard._IMAP_HOST:
            return imap
        return False

    monkeypatch.setattr(net_guard, "_tcp_connect", fake_tcp)
    monkeypatch.setattr(net_guard.os.path, "ismount", lambda path: nas)


def test_bridge_degraded_and_nas_absent_are_nonfatal(monkeypatch, caplog):
    _patch_network(monkeypatch, outbound=True, imap=True, nas=False)
    monkeypatch.setattr(
        net_guard.urllib.request,
        "urlopen",
        lambda req, timeout: _Response({"overall": "fail"}),
    )

    ok, reasons = net_guard.network_ok(
        bridge_url="http://bridge.local:9100", bridge_token="token")

    assert ok is True
    assert any(r.startswith("bridge:degraded") for r in reasons)
    assert any(r.startswith("nas:degraded") for r in reasons)
    assert "scan-aborting" not in "\n".join(caplog.messages)


def test_bridge_unreachable_is_nonfatal(monkeypatch):
    _patch_network(monkeypatch, outbound=True, imap=True, nas=True)

    def raise_url_error(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(net_guard.urllib.request, "urlopen", raise_url_error)

    ok, reasons = net_guard.network_ok(bridge_url="http://bridge.local:9100")

    assert ok is True
    assert any(r.startswith("bridge:degraded") for r in reasons)


def test_outbound_failure_is_fatal(monkeypatch):
    _patch_network(monkeypatch, outbound=False, imap=True, nas=True)
    monkeypatch.setattr(
        net_guard.urllib.request,
        "urlopen",
        lambda req, timeout: _Response({"overall": "ok"}),
    )

    ok, reasons = net_guard.network_ok(bridge_url="http://bridge.local:9100")

    assert ok is False
    assert any(r.startswith("outbound:fail") for r in reasons)


def test_imap_failure_is_fatal(monkeypatch):
    _patch_network(monkeypatch, outbound=True, imap=False, nas=True)
    monkeypatch.setattr(
        net_guard.urllib.request,
        "urlopen",
        lambda req, timeout: _Response({"overall": "ok"}),
    )

    ok, reasons = net_guard.network_ok(bridge_url="http://bridge.local:9100")

    assert ok is False
    assert any(r.startswith("imap:fail") for r in reasons)


def test_orchestrator_disables_bridge_actions_when_bridge_degraded():
    agent_root = Path(__file__).resolve().parents[1] / "agent"
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))

    from app.orchestrator import Orchestrator

    orch = object.__new__(Orchestrator)
    orch.bridge_ok = False
    orch.mode = "draft_only"

    assert orch._action_allowed("imessage") is False
    assert orch._action_allowed("pdf_route") is True
