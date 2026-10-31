"""Minimal read-only cgroup usage for VPS Monitor, without trading internals.

cpuVcpu is the *mean number of CPU cores consumed* between calls, not a
host- or billing-account-wide utilization. Memory is cgroup memory.current.
The endpoint deliberately reveals no env, positions, credentials or trades.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path
import threading
import time

_MIB = 1024.0 * 1024.0


class ContainerResourceSampler:
    def __init__(self, root: str = "/sys/fs/cgroup", clock=time.monotonic):
        self.root = Path(root)
        self.clock = clock
        self.lock = threading.Lock()
        self.prev: tuple[float, float] | None = None

    def _read_cpu(self) -> tuple[float, str]:
        try:
            lines = (self.root / "cpu.stat").read_text().splitlines()
            usage = next(int(s.split()[1]) for s in lines
                         if s.startswith("usage_usec "))
            return usage / 1_000_000.0, "CGROUP_V2"
        except (OSError, StopIteration, ValueError):
            return time.process_time(), "PROCESS"

    def _read_ram(self) -> tuple[float, float | None, str]:
        try:
            current = int((self.root / "memory.current").read_text().strip())
            limit = (self.root / "memory.max").read_text().strip()
            limit_mb = None if limit == "max" else int(limit) / _MIB
            return round(current / _MIB, 2), (
                round(limit_mb, 2) if limit_mb is not None and limit_mb > 0 else None
            ), "CGROUP_V2"
        except (OSError, ValueError):
            try:
                # Current RSS, not the peak ru_maxrss.
                line = next(line for line in Path("/proc/self/status").read_text().splitlines()
                            if line.startswith("VmRSS:"))
                return round(int(line.split()[1]) / 1024.0, 2), None, "PROCESS"
            except (OSError, ValueError, StopIteration):
                return 0.0, None, "UNAVAILABLE"

    def snapshot(self) -> dict:
        with self.lock:
            now = self.clock()
            cpu_s, source_cpu = self._read_cpu()
            cpu_vcpu = None
            if self.prev is not None:
                then, used = self.prev
                if now > then and cpu_s >= used:
                    cpu_vcpu = round(max(0.0, (cpu_s - used) / (now - then)), 5)
            self.prev = now, cpu_s
            ram_mb, limit_mb, source_ram = self._read_ram()
        return {
            "schema": 1,
            "service": "autotrade",
            "sampledAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "cpuVcpu": cpu_vcpu,
            "ramUsedMb": ram_mb,
            "ramLimitMb": limit_mb,
            "sourceCpu": source_cpu,
            "sourceRam": source_ram,
        }


container_resource_sampler = ContainerResourceSampler()
