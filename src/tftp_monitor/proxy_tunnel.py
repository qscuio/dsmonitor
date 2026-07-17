from __future__ import annotations

import json
import select
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import paramiko

from .proxy_credentials import read_password


SSH_EXE = r"C:\Windows\System32\OpenSSH\ssh.exe"
STARTUP_BATCH_NAME = "dsmonitor-proxy-agent.cmd"
CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class ProxyTunnel:
    name: str
    local_host: str
    local_port: int
    remote_host: str
    remote_port: int
    jump_user: str
    jump_host: str
    enabled: bool = True


@dataclass(frozen=True)
class ProxyConfig:
    tunnels: list[ProxyTunnel]


def default_proxy_config() -> ProxyConfig:
    return ProxyConfig(
        tunnels=[
            ProxyTunnel(
                name="N2X",
                local_host="127.0.0.1",
                local_port=13389,
                remote_host="10.71.20.231",
                remote_port=3389,
                jump_user="tsl",
                jump_host="10.71.1.3",
                enabled=True,
            ),
            ProxyTunnel(
                name="M4000",
                local_host="127.0.0.1",
                local_port=10004,
                remote_host="10.71.20.230",
                remote_port=10004,
                jump_user="tsl",
                jump_host="10.71.1.3",
                enabled=True,
            ),
            ProxyTunnel(
                name="M4000-23",
                local_host="127.0.0.1",
                local_port=20023,
                remote_host="10.71.20.134",
                remote_port=23,
                jump_user="tsl",
                jump_host="10.71.1.3",
                enabled=True,
            ),
        ]
    )


def proxy_config_path(app_data_dir: Path) -> Path:
    return app_data_dir / "proxies.json"


def proxy_log_path(app_data_dir: Path) -> Path:
    return app_data_dir / "proxy-agent.log"


def build_ssh_command(tunnel: ProxyTunnel) -> list[str]:
    forward = f"{tunnel.local_host}:{tunnel.local_port}:{tunnel.remote_host}:{tunnel.remote_port}"
    return [
        SSH_EXE,
        "-N",
        "-L",
        forward,
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        f"{tunnel.jump_user}@{tunnel.jump_host}",
    ]


def build_proxy_startup_batch_content(exe_path: Path) -> str:
    return f'@echo off\nstart "" "{exe_path.resolve()}"\n'


def proxy_startup_batch_path() -> Path:
    appdata = Path.home() / "AppData" / "Roaming"
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / STARTUP_BATCH_NAME


def duplicate_tunnel_name(config: ProxyConfig, name: str, ignore_index: int | None = None) -> str | None:
    normalized = name.strip().casefold()
    for index, tunnel in enumerate(config.tunnels):
        if ignore_index is not None and index == ignore_index:
            continue
        if tunnel.name.strip().casefold() == normalized:
            return tunnel.name
    return None


def duplicate_local_endpoint(
    config: ProxyConfig,
    local_host: str,
    local_port: int,
    ignore_index: int | None = None,
) -> str | None:
    normalized_host = local_host.strip().casefold()
    for index, tunnel in enumerate(config.tunnels):
        if ignore_index is not None and index == ignore_index:
            continue
        if tunnel.local_host.strip().casefold() == normalized_host and tunnel.local_port == local_port:
            return tunnel.name
    return None


def deduplicate_tunnels(config: ProxyConfig) -> ProxyConfig:
    seen_names: set[str] = set()
    seen_tunnels: set[tuple[object, ...]] = set()
    tunnels: list[ProxyTunnel] = []
    for tunnel in config.tunnels:
        tunnel_key = (
            tunnel.local_host.strip().casefold(),
            tunnel.local_port,
            tunnel.remote_host.strip().casefold(),
            tunnel.remote_port,
            tunnel.jump_user.strip().casefold(),
            tunnel.jump_host.strip().casefold(),
            tunnel.enabled,
        )
        if tunnel_key in seen_tunnels:
            continue
        seen_tunnels.add(tunnel_key)
        base_name = tunnel.name.strip() or "Tunnel"
        candidate = base_name
        suffix = 2
        while candidate.casefold() in seen_names:
            candidate = f"{base_name}-{suffix}"
            suffix += 1
        seen_names.add(candidate.casefold())
        if candidate == tunnel.name:
            tunnels.append(tunnel)
        else:
            tunnels.append(
                ProxyTunnel(
                    name=candidate,
                    local_host=tunnel.local_host,
                    local_port=tunnel.local_port,
                    remote_host=tunnel.remote_host,
                    remote_port=tunnel.remote_port,
                    jump_user=tunnel.jump_user,
                    jump_host=tunnel.jump_host,
                    enabled=tunnel.enabled,
                )
            )
    return ProxyConfig(tunnels=tunnels)


def load_proxy_config(path: Path) -> ProxyConfig:
    if not path.exists():
        return default_proxy_config()
    data = json.loads(path.read_text(encoding="utf-8"))
    tunnels = [ProxyTunnel(**item) for item in data.get("tunnels", [])]
    return deduplicate_tunnels(ProxyConfig(tunnels=tunnels))


def save_proxy_config(path: Path, config: ProxyConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"tunnels": [asdict(tunnel) for tunnel in config.tunnels]}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def ensure_proxy_config(path: Path) -> ProxyConfig:
    config = load_proxy_config(path)
    if not path.exists():
        save_proxy_config(path, config)
        return config
    saved_data = json.loads(path.read_text(encoding="utf-8"))
    saved_config = ProxyConfig(tunnels=[ProxyTunnel(**item) for item in saved_data.get("tunnels", [])])
    if saved_config != config:
        save_proxy_config(path, config)
    return config


def is_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class PasswordProxyTunnelProcess:
    def __init__(
        self,
        tunnel: ProxyTunnel,
        password: str,
        log: Callable[[str], None],
    ) -> None:
        self.tunnel = tunnel
        self.password = password
        self.log = log
        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._exit_code: int | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, name=f"proxy-{self.tunnel.name}", daemon=True)
        self._thread.start()

    def poll(self) -> int | None:
        if self._thread and self._thread.is_alive():
            return None
        return self._exit_code

    def terminate(self) -> None:
        self._stop.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> int | None:
        if self._thread:
            self._thread.join(timeout)
        return self.poll()

    def kill(self) -> None:
        self.terminate()

    def _serve(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                self._server = server
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.tunnel.local_host, self.tunnel.local_port))
                server.listen(20)
                server.settimeout(1.0)
                self.log(f"{self.tunnel.name} listening on {self.tunnel.local_host}:{self.tunnel.local_port}")
                while not self._stop.is_set():
                    try:
                        client, address = server.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    threading.Thread(
                        target=self._handle_client,
                        args=(client, address),
                        name=f"proxy-client-{self.tunnel.name}",
                        daemon=True,
                    ).start()
            self._exit_code = 0
        except Exception as exc:
            self._exit_code = 1
            self.log(f"{self.tunnel.name} password tunnel failed: {exc}")

    def _handle_client(self, client: socket.socket, address) -> None:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        channel = None
        try:
            ssh.connect(
                hostname=self.tunnel.jump_host,
                username=self.tunnel.jump_user,
                password=self.password,
                timeout=10,
                banner_timeout=10,
                auth_timeout=10,
                look_for_keys=False,
                allow_agent=False,
            )
            transport = ssh.get_transport()
            if transport is None:
                raise RuntimeError("SSH transport is not available")
            channel = transport.open_channel(
                "direct-tcpip",
                (self.tunnel.remote_host, self.tunnel.remote_port),
                address,
            )
            self._relay(client, channel)
        except Exception as exc:
            self.log(f"{self.tunnel.name} connection failed: {exc}")
        finally:
            try:
                client.close()
            except OSError:
                pass
            if channel is not None:
                channel.close()
            ssh.close()

    def _relay(self, client: socket.socket, channel: Any) -> None:
        sockets = [client, channel]
        while not self._stop.is_set():
            readable, _, _ = select.select(sockets, [], [], 1.0)
            if client in readable:
                data = client.recv(32768)
                if not data:
                    break
                channel.sendall(data)
            if channel in readable:
                data = channel.recv(32768)
                if not data:
                    break
                client.sendall(data)


class ProxyProcessManager:
    def __init__(
        self,
        app_data_dir: Path,
        port_checker: Callable[[str, int], bool] = is_port_open,
        popen_factory: Callable[[list[str]], Any] | None = None,
        password_lookup: Callable[[ProxyTunnel], str | None] | None = None,
    ) -> None:
        self.app_data_dir = app_data_dir
        self.port_checker = port_checker
        self.popen_factory = popen_factory or self._popen
        self.password_lookup = password_lookup or (lambda tunnel: read_password(tunnel.name))
        self.processes: dict[str, Any] = {}

    @staticmethod
    def _popen(command: list[str]) -> subprocess.Popen:
        return subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )

    def start(self, tunnel: ProxyTunnel) -> str:
        if tunnel.name in self.processes and self.processes[tunnel.name].poll() is None:
            return f"{tunnel.name} already started"
        if self.port_checker(tunnel.local_host, tunnel.local_port):
            return f"{tunnel.local_host}:{tunnel.local_port} already listening"
        password = self.password_lookup(tunnel)
        if password:
            process = PasswordProxyTunnelProcess(tunnel, password, self.log)
            process.start()
            self.processes[tunnel.name] = process
            self.log(f"started {tunnel.name} with password backend")
            return f"Started {tunnel.name}"
        command = build_ssh_command(tunnel)
        self.processes[tunnel.name] = self.popen_factory(command)
        self.log(f"started {tunnel.name}: {' '.join(command)}")
        return f"Started {tunnel.name}"

    def stop(self, tunnel: ProxyTunnel) -> str:
        process = self.processes.get(tunnel.name)
        if process and process.poll() is None:
            process.terminate()
            self.processes.pop(tunnel.name, None)
            self.log(f"stopped {tunnel.name}")
            return f"Stopped {tunnel.name}"
        self.processes.pop(tunnel.name, None)
        return f"{tunnel.name} is not started"

    def stop_all(self) -> None:
        for name, process in list(self.processes.items()):
            if process.poll() is None:
                self.log(f"stopping {name}")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
        self.processes.clear()

    def status(self, tunnel: ProxyTunnel) -> str:
        process = self.processes.get(tunnel.name)
        if process and process.poll() is None:
            return "started"
        if self.port_checker(tunnel.local_host, tunnel.local_port):
            return "listening"
        return "stopped"

    def reap(self) -> None:
        for name, process in list(self.processes.items()):
            code = process.poll()
            if code is not None:
                self.log(f"{name} exited with code {code}")
                del self.processes[name]

    def log(self, message: str) -> None:
        path = proxy_log_path(self.app_data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")


def run_proxy_agent(app_data_dir: Path, poll_seconds: int = 10) -> None:
    manager = ProxyProcessManager(app_data_dir)
    config_path = proxy_config_path(app_data_dir)
    manager.log("proxy agent started")
    try:
        while True:
            config = ensure_proxy_config(config_path)
            enabled_names = {tunnel.name for tunnel in config.tunnels if tunnel.enabled}
            for tunnel in config.tunnels:
                if tunnel.enabled:
                    manager.start(tunnel)
            for name, process in list(manager.processes.items()):
                if name not in enabled_names:
                    manager.log(f"{name} disabled in config")
                    process.terminate()
                    del manager.processes[name]
            manager.reap()
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        manager.log("proxy agent interrupted")
    finally:
        manager.stop_all()


def current_exe_path() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()
