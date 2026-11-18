"""The clipboard on every system: through the terminal, as Textual copies, and on Windows through Windows' own too.

Textual copies through the terminal alone, with the OSC 52 escape code, which Windows Terminal, iTerm2 and kitty read
and the classic Windows console window and macOS Terminal do not: there a copy said it was done and copied nothing. On
Windows the text goes to Windows' own clipboard as well, through its API, which every console reaches.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Protocol


class Copies(Protocol):
    """What copies through the terminal: Textual's `App`."""

    def copy_to_clipboard(self, text: str) -> None: ...


def copy_text(app: Copies, text: str, *, windows: bool = os.name == "nt") -> bool:
    """Copy `text` through the terminal and, on Windows, into Windows' own clipboard, its lines ended in CR LF as
    Windows ends them. True where the text is known to be there: Windows' clipboard took it. Through the terminal
    alone no program can know."""
    app.copy_to_clipboard(text)
    return windows and _windows_copy(text.replace("\r\n", "\n").replace("\n", "\r\n"))


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
