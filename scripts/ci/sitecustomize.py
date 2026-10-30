"""Offline test subprocesses cannot reach BingX, Supabase or any TCP host."""
import socket

def denied(*args, **kwargs):
    raise RuntimeError('External network disabled in integration tests')

socket.create_connection = denied
socket.socket.connect = denied
socket.socket.connect_ex = denied
