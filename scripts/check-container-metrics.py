"""Offline tests for container resource sampler; no trade or network calls."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from autotrade_v2.app.monitor_resources import ContainerResourceSampler

with tempfile.TemporaryDirectory() as folder:
    cgroup=Path(folder)
    (cgroup/'cpu.stat').write_text('usage_usec 1000000\n')
    (cgroup/'memory.current').write_text(str(128*1024*1024))
    (cgroup/'memory.max').write_text(str(512*1024*1024))
    clock=[100.0]
    sampler=ContainerResourceSampler(root=folder,clock=lambda:clock[0])
    cold=sampler.snapshot()
    assert cold['cpuVcpu'] is None
    assert cold['ramUsedMb']==128.0
    assert cold['ramLimitMb']==512.0
    assert cold['sourceCpu']=='CGROUP_V2'
    assert cold['sourceRam']=='CGROUP_V2'
    clock[0]=102.0
    (cgroup/'cpu.stat').write_text('usage_usec 2000000\n')
    warm=sampler.snapshot()
    assert warm['cpuVcpu']==0.5,warm
    assert warm['ramUsedMb']==128.0
    (cgroup/'memory.max').write_text('max')
    assert sampler.snapshot()['ramLimitMb'] is None
print('PASS: AutoTrade cgroup memory/CPU unit test (cold start and delta sampling)')
