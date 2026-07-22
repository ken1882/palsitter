from __future__ import annotations

import platform
import subprocess

import psutil


def pids_listening_on(port: int, proto: str = "tcp") -> set[int]:
    """Return process IDs listening on the requested local port."""
    protocol = proto.lower()
    if protocol not in {"tcp", "udp"}:
        raise ValueError(f"Unsupported protocol: {proto}")

    try:
        connections = psutil.net_connections(kind=protocol)
    except (OSError, psutil.Error):
        return set()

    pids = set()
    for connection in connections:
        address = connection.laddr
        if not address:
            continue
        address_port = getattr(address, "port", None)
        if address_port is None:
            address_port = address[1]
        if address_port != port:
            continue
        if protocol == "tcp" and connection.status != psutil.CONN_LISTEN:
            continue
        if connection.pid is not None:
            pids.add(connection.pid)
    return pids


def kill_process_tree(pid: int, grace: float = 3.0) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        children = parent.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        children = []
    processes = [*children, parent]
    for process in processes:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(processes, timeout=grace)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def kill_by_port(port: int, proto: str = "tcp", grace: float = 5.0) -> list[int]:
    """
    Find processes listening on `port` and kill their trees.
    Returns list of PIDs targeted. Falls back to OS tools if psutil can’t see them.
    """
    pids = pids_listening_on(port, proto=proto)

    # Fallbacks if nothing found (limited privileges):
    if not pids:
        system = platform.system()
        try:
            if system == "Windows":
                # netstat -ano | findstr :<port>
                out = subprocess.check_output(
                    ["netstat", "-ano"], text=True, errors="ignore"
                )
                for line in out.splitlines():
                    if f":{port} " in line and "LISTEN" in line.upper():
                        parts = line.split()
                        pid = int(parts[-1])
                        pids.add(pid)
            else:
                # lsof -iTCP:<port> -sTCP:LISTEN -t
                out = subprocess.check_output(
                    ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                    text=True,
                    errors="ignore",
                )
                for line in out.split():
                    pids.add(int(line.strip()))
        except Exception:
            pass
    for pid in list(pids):
        print(f"Killing {pid}")
        kill_process_tree(pid, grace=grace)
    return sorted(pids)
