"""A project's own rule: a test that reaches the network is slow, flaky and leaks data; fake it at the boundary."""

from __future__ import annotations

from ..rule_helpers import ChangedFile, FileRule, calls_in, is_test

#: The libraries a test is likeliest to reach the network through, and their calls that open a connection, as the code
#: writes them. A client built is no connection opened: `httpx.Client(transport=MockTransport(...))` is the fake this
#: rule asks for.
LIBRARIES = ("requests", "httpx", "urllib.request", "aiohttp", "socket")
CONNECTS = (
    "get", "post", "put", "delete", "patch", "head", "options", "request", "urlopen", "create_connection", "socket",
)  # fmt: skip
CALLS = tuple(f"{library}.{call}" for library in LIBRARIES for call in CONNECTS)


class OfflineTests(FileRule):
    """The suite that proves this reviewer runs offline, on a fake model; a project's tests should too. A fake at
    the boundary, a stub client or a recorded answer, keeps a test fast, repeatable and private. Read from the module,
    as Python reads a call: one a comment, a string or a docstring names is none, and one a formatter wrapped over
    lines is found where it starts."""

    id = "tests-offline"
    severity = "major"
    reads_tests = True  # tests alone, which `check` chooses
    title = "a test reaches the network"
    fix = "Fake the network at the boundary: a stub client or a recorded answer, never a live call."
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
