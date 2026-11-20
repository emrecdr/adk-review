"""The clipboard on every system: through the terminal, as Textual copies, and on Windows and macOS through the
system's own too.

Textual copies through the terminal alone, with the OSC 52 escape code, which Windows Terminal, iTerm2 and kitty read
and the classic Windows console window and macOS Terminal do not: there a copy said it was done and copied nothing. On
Windows the text goes to Windows' own clipboard as well, through its API, which every console reaches, and on macOS to
the Mac's, through `pbcopy`, unless the screen runs over SSH.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Protocol


class Copies(Protocol):
    """What copies through the terminal: Textual's `App`."""

    def copy_to_clipboard(self, text: str) -> None: ...


#: macOS' own clipboard program, by its full path: never one a folder on PATH holds.
PBCOPY = "/usr/bin/pbcopy"


def copy_text(app: Copies, text: str, *, windows: bool = os.name == "nt", mac: bool = sys.platform == "darwin") -> bool:
    """Copy `text` through the terminal and into the system's own clipboard: on Windows, its lines ended in CR LF as
    Windows ends them, and on macOS where the screen runs on the Mac itself. Over SSH the Mac's clipboard is the far
    machine's, and the terminal alone reaches the person's. True where the text is known to be there: the system's
    clipboard took it. Through the terminal alone no program can know."""
    app.copy_to_clipboard(text)
    if windows:
        return _windows_copy(text.replace("\r\n", "\n").replace("\n", "\r\n"))
    return mac and not os.environ.get("SSH_CONNECTION") and _mac_copy(text)


def _mac_copy(text: str) -> bool:
    """`text` into the Mac's own clipboard, through `pbcopy`; False where it did not take it. It reads its input in the
    locale's encoding: measured, with no locale or `LANG=C` it read UTF-8 as Mac Roman, an accented letter pasted as
    two others, so it is told UTF-8 through `LC_ALL`, which wins over every other setting."""
    try:
        done = subprocess.run(
            [PBCOPY],
            input=text.encode("utf-8"),
            env={**os.environ, "LC_ALL": "en_US.UTF-8"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except OSError, subprocess.SubprocessError:  # not there, or no answer
        return False
    return done.returncode == 0


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _CF_UNICODETEXT = 13
    _GMEM_MOVEABLE = 0x0002
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.argtypes = []
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.CloseClipboard.argtypes = []
    _user32.CloseClipboard.restype = wintypes.BOOL
    _kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalFree.restype = wintypes.HGLOBAL

    def _windows_copy(text: str) -> bool:
        """`text` into Windows' own clipboard as Unicode text; False where the clipboard could not be had."""
        data = text.encode("utf-16-le") + b"\0\0"
        for _attempt in range(10):  # another program can hold the clipboard open for a moment
            if _user32.OpenClipboard(None):
                break
            time.sleep(0.02)
        else:
            return False
        try:
            if not _user32.EmptyClipboard():
                return False
            handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            pointer = _kernel32.GlobalLock(handle)
            if not pointer:
                _kernel32.GlobalFree(handle)
                return False
            ctypes.memmove(pointer, data, len(data))
            _kernel32.GlobalUnlock(handle)
            if not _user32.SetClipboardData(_CF_UNICODETEXT, handle):
                _kernel32.GlobalFree(handle)
                return False
            return True  # the clipboard owns the memory now
        finally:
            _user32.CloseClipboard()

else:

    def _windows_copy(text: str) -> bool:  # noqa: ARG001 -- the Windows function's signature
        """No Windows clipboard on this system."""
        return False
