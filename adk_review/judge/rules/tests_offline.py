"""A project's own rule: a test that reaches the network is slow, flaky and leaks data; fake it at the boundary."""

from __future__ import annotations

from ..rule_helpers import ChangedFile, FileRule, calls_in, is_test

#: The calls a test is likeliest to reach the network through, each one that opens a connection where it is called,
#: as the code writes it, checked in the packages themselves. A client or a socket built is no connection opened:
#: `httpx.Client(transport=MockTransport(...))` is the fake this rule asks for, and `socket.socket()` opens nothing
#: until it connects.
VERBS = ("get", "post", "put", "delete", "patch", "head", "options", "request")
CALLS = (
    *(f"{library}.{verb}" for library in ("requests", "httpx") for verb in VERBS),
    "httpx.stream",
    "urllib.request.urlopen",
    "urllib.request.urlretrieve",
    "urllib3.request",
    "aiohttp.request",
    "socket.create_connection",
    "asyncio.open_connection",
    "websockets.connect",
    "websockets.asyncio.client.connect",
    "websockets.client.connect",
    "websockets.sync.client.connect",
)


class OfflineTests(FileRule):
    """The suite that proves this reviewer runs offline, on a fake model; a project's tests should too. A fake at
    the boundary, a stub client or a recorded answer, keeps a test fast, repeatable and private. Read from the module,
    as Python reads a call: one a comment, a string or a docstring names is none, and one a formatter wrapped over
    lines is found where it starts. Not found, as known limits: a call through a name imported from the library,
    `from requests import get`, or through an alias, `import requests as rq`, and one on what a call returned,
    `requests.Session().get`."""

    id = "tests-offline"
    severity = "major"
    reads_tests = True  # tests alone, which `check` chooses
    title = "a test reaches the network"
    fix = "Fake the network at the boundary: a stub client or a recorded answer, never a live call."
    why = "A test that needs the network fails when the network is down, runs slowly, and can send data out."
    bad = (
        ("tests/test_a.py", "r = requests.get(url)\n"),
        ("tests/test_a.py", "s = socket.create_connection(\n    (h, p),\n)\n"),
    )
    good = (
        ("tests/test_a.py", "client = httpx.Client(transport=httpx.MockTransport(handler))\n"),
        ("tests/test_a.py", "# requests.get(url) is faked above\n"),
        ("src/client.py", "requests.get(url)\n"),
    )

    def check(self, file: ChangedFile) -> list[int]:
        """Each line where a call the change added to a test starts: the report quotes that line."""
        if not file.path.endswith(".py") or not is_test(file.path):
            return []
        return [call.lineno for call in calls_in(file.module, *CALLS) if call.lineno in file.added]
