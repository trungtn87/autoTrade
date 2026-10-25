"""Streamlit entrypoint: execute dashboard on every script rerun."""
import runpy

runpy.run_module('autotrade_live.app', run_name='__main__')
