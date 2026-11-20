"""The flat Lab 10 L2 module must import with no DuckDB or pyarrow installed."""
from __future__ import annotations

import subprocess
import sys
import textwrap


def test_l2_lab10_imports_without_duckdb_or_pyarrow() -> None:
    code = textwrap.dedent("""
        import sys
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name.split('.')[0] in ('duckdb', 'pyarrow'):
                    raise ImportError('blocked ' + name)
        sys.meta_path.insert(0, Block())
        import autotrade_v3.live.l2_lab10 as m
        assert hasattr(m, 'V3Lab10Strategy')
        print('OK')
    """)
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout
