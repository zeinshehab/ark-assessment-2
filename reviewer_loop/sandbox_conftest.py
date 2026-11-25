"""Copied into the sandbox directory as `conftest.py`. pytest loads it BEFORE importing any test
or solution code, so this audit hook is active while untrusted code runs.

Python's audit hooks (PEP 578) fire on sensitive operations; raising from the hook blocks them.
Hooks cannot be removed once added. This is a guard rail, not a security boundary: native code
can bypass it. See NOTES.md, Sandbox section.

Policy:
  * no network, no subprocesses, no native libraries, no registry
  * writes / deletes / renames only inside the sandbox directory
  * reads only inside the sandbox directory and the Python installation (so e.g. ~/.env is unreadable)
"""
import os
import sys


def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


_SANDBOX_DIR = _norm(os.getcwd())
_READ_ROOTS = [_SANDBOX_DIR] + [_norm(p) for p in {sys.prefix, sys.base_prefix, sys.exec_prefix}]
_BLOCKED_EVENTS = {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen", "os.system",
                   "os.startfile", "os.kill", "ctypes.dlopen", "winreg.OpenKey", "urllib.Request"}
_BLOCKED_PREFIXES = ("os.exec", "os.spawn", "os.posix_spawn")
_WRITE_EVENTS = {"os.remove", "os.rename", "os.rmdir", "shutil.rmtree", "os.chmod", "os.mkdir"}
_LIST_EVENTS = {"os.listdir", "os.scandir"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND


def _allowed(path, roots) -> bool:
    if path is None or isinstance(path, int):  # cwd or an already-open file descriptor
        return True
    if isinstance(path, bytes):
        path = path.decode(errors="replace")
    if str(path).lower() in (os.devnull.lower(), "\\\\.\\nul"):  # the null device is harmless
        return True
    target = _norm(path)
    for root in roots:
        try:
            if os.path.commonpath([target, root]) == root:  # commonpath, not startswith: "/sb2" is not in "/sb"
                return True
        except ValueError:  # different drives on Windows
            pass
    return False


def _guard(event, args):
    if event in _BLOCKED_EVENTS or event.startswith(_BLOCKED_PREFIXES):
        raise PermissionError(f"sandbox: '{event}' is not allowed")
    if event == "open":
        path, mode, flags = args
        writing = any(c in mode for c in "wax+") if isinstance(mode, str) else bool((flags or 0) & _WRITE_FLAGS)
        if not _allowed(path, [_SANDBOX_DIR] if writing else _READ_ROOTS):
            raise PermissionError(f"sandbox: {'writing' if writing else 'reading'} {path} is not allowed")
    elif event in _WRITE_EVENTS and args and not _allowed(args[0], [_SANDBOX_DIR]):
        raise PermissionError(f"sandbox: '{event}' outside the sandbox is not allowed")
    elif event in _LIST_EVENTS and args and not _allowed(args[0], _READ_ROOTS):
        raise PermissionError(f"sandbox: listing {args[0]} is not allowed")


sys.addaudithook(_guard)
