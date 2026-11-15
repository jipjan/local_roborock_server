import subprocess

import pytest

from roborock_local_server.config import TurnConfig
from roborock_local_server.turn_relay import EmbeddedTurnRelay
from roborock_local_server import turn_relay


@pytest.mark.parametrize("mode", ["disabled", "external"])
def test_other_modes_do_not_start_coturn(tmp_path, monkeypatch, mode):
    def unexpected(*args, **kwargs):
        pytest.fail("coturn should not start")
    monkeypatch.setattr(turn_relay.subprocess, "Popen", unexpected)
    relay = EmbeddedTurnRelay(TurnConfig(mode=mode), tmp_path)
    relay.start()
    assert not relay.running


def test_failed_coturn_launch_does_not_leave_process(tmp_path, monkeypatch):
    monkeypatch.setattr(turn_relay.socket, "getaddrinfo", lambda *args: [(None, None, None, None, ("192.0.2.1", 3478))])
    def missing(*args, **kwargs):
        raise FileNotFoundError("coturn unavailable")
    monkeypatch.setattr(turn_relay.subprocess, "Popen", missing)
    relay = EmbeddedTurnRelay(TurnConfig(mode="provided", host="turn.example.com", username="user", password="secret", realm="example.com"), tmp_path)
    with pytest.raises(FileNotFoundError):
        relay.start()
    assert not relay.running


def test_stop_kills_relay_that_ignores_termination(tmp_path):
    class Process:
        terminated = False
        killed = False
        def terminate(self):
            self.terminated = True
        def wait(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("turnserver", timeout)
        def kill(self):
            self.killed = True
    relay = EmbeddedTurnRelay(TurnConfig(), tmp_path)
    process = Process()
    relay.process = process
    relay.stop()
    assert process.terminated and process.killed
    assert relay.process is None


def _watchdog_supervisor(tmp_path, relay):
    import asyncio
    import types
    from roborock_local_server import server as server_module
    sup = types.SimpleNamespace(
        _stop_event=asyncio.Event(),
        _turn_relay=relay,
        config=types.SimpleNamespace(turn=types.SimpleNamespace(host="turn.example.com", port=3478)),
        root_logger=types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        _refresh_turn_health=lambda: None,
    )
    sup._turn_watchdog_loop = types.MethodType(server_module.ReleaseSupervisor._turn_watchdog_loop, sup)
    return sup, server_module


class FakeRelay:
    def __init__(self, running=False, ip="192.0.2.1", resolves="192.0.2.1"):
        self._running = running
        self.external_ip = ip
        self.resolves = resolves
        self.starts = 0
    @property
    def running(self):
        return self._running
    def resolve_external_ip(self):
        return self.resolves
    def stop(self):
        self._running = False
    def start(self):
        self.starts += 1
        self._running = True
        self.external_ip = self.resolves


def _run_watchdog(tmp_path, monkeypatch, relay, seconds=0.2):
    import asyncio
    sup, server_module = _watchdog_supervisor(tmp_path, relay)
    monkeypatch.setattr(server_module, "TURN_WATCHDOG_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(server_module, "TURN_IP_CHECK_SECONDS", 0.0)

    async def run():
        task = asyncio.create_task(sup._turn_watchdog_loop())
        await asyncio.sleep(seconds)
        sup._stop_event.set()
        await task
    asyncio.run(run())


def test_watchdog_starts_coturn_again_after_it_exited(tmp_path, monkeypatch):
    relay = FakeRelay(running=False)
    _run_watchdog(tmp_path, monkeypatch, relay)
    assert relay.starts == 1 and relay.running


def test_watchdog_restarts_coturn_when_the_public_ip_changes(tmp_path, monkeypatch):
    relay = FakeRelay(running=True, ip="192.0.2.1", resolves="198.51.100.7")
    _run_watchdog(tmp_path, monkeypatch, relay)
    assert relay.starts == 1 and relay.external_ip == "198.51.100.7"


def test_watchdog_leaves_a_healthy_relay_alone(tmp_path, monkeypatch):
    relay = FakeRelay(running=True)
    _run_watchdog(tmp_path, monkeypatch, relay)
    assert relay.starts == 0
