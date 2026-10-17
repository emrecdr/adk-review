"""One-time GitHub Copilot login for the reviewer.

`adk-review-login`, or `uv run adk-review-login` in the checkout. Copilot is the arm
with no API key. GitHub's OAuth device flow prints a URL
and a short code; you approve it in a browser; the token is cached on disk,
after which the reviewer runs unattended. LiteLLM ships this flow but polls
for only sixty seconds per code, which is shorter than walking to a browser,
so this script runs the same flow with GitHub's own window (about fifteen
minutes), using LiteLLM's endpoints, client id, headers and token file so the
reviewer reads the result exactly as if LiteLLM had written it. A module of the package, so `uv tool install`
puts the command on `PATH` beside `adk-review`: measured by walking a first run, the refusal once named a script
of the checkout, which an installed reviewer has no way to run.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

# The token's path as the reviewer reads it, one spelling for both; and importing it puts the operating system's
# certificate store into `ssl` before any request, as the reviewer does before any client is built.
from .judge.config import copilot_token as token_path

ROOT = Path(__file__).resolve().parents[1]


def keep_private(directory: Path) -> None:
    """The token's folder readable by its owner alone. Measured before: written with the default umask, both token
    files were 0644 in a 0755 folder, readable by every account on the machine."""
    directory.chmod(0o700)
    for path in directory.iterdir():
        if path.is_file():
            path.chmod(0o600)


def unreachable(exc: Exception) -> int:
    """GitHub could not be reached: the reason, and the fix where the reason is a certificate. Measured on a company
    Windows machine: the proxy re-signs HTTPS with a root certificate Windows trusts and Python's own list does not,
    so `uv` installed everything and the first request to GitHub ended in a traceback."""
    print(f"[copilot] GitHub could not be reached: {exc}", file=sys.stderr)
    if "CERTIFICATE_VERIFY_FAILED" in str(exc):
        print(
            "[copilot] a proxy on this network re-signs HTTPS: point Python at its root certificate with "
            "SSL_CERT_FILE, a PEM bundle that holds it (the README's Windows notes say how), then run this again",
            file=sys.stderr,
        )
    return 1


def main(argv: list[str]) -> int:
    # Every file this process makes is its owner's alone from its first byte: LiteLLM writes the token with the default
    # umask, and measured, it stayed readable by every account for the network exchange before `keep_private` ran.
    os.umask(0o077)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # a Windows console is not UTF-8 by default
    # A flag it does not know, `--help` among them, stops here, never a login: measured, `--help` began one.
    parser = argparse.ArgumentParser(prog="adk-review-login", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--force", action="store_true", help="log in again although a token is cached")
    args = parser.parse_args(argv)
    try:  # the reviewer reads `.env`; the login reads of it what names Copilot's endpoints, an enterprise tenant's say
        for key, value in dotenv_values(ROOT / ".env").items():
            if key.startswith("GITHUB_COPILOT_") and value:
                os.environ.setdefault(key, value)  # the shell wins over the file, as it does for the reviewer
    except UnicodeDecodeError:
        print(
            "[copilot] the central .env is not UTF-8; save it as UTF-8 (Windows PowerShell 5.1's `>` writes UTF-16)",
            file=sys.stderr,
        )
        return 1
    token = token_path()
    if token.is_file() and token.stat().st_size and not args.force:  # an empty file is no token, to the reviewer too
        print(f"[copilot] already authenticated: {token}")
        print("[copilot] re-authenticate with: adk-review-login --force")
        return 0
    import httpx
    from litellm.llms.github_copilot.authenticator import (
        DEFAULT_GITHUB_ACCESS_TOKEN_URL,
        DEFAULT_GITHUB_CLIENT_ID,
        Authenticator,
    )
    from litellm.llms.github_copilot.common_utils import GetAPIKeyError, GetDeviceCodeError

    auth = Authenticator()
    try:
        info = auth._get_device_code()  # LiteLLM's request: its client id, its headers
    except GetDeviceCodeError as exc:
        return unreachable(exc)
    expires_in = int(info.get("expires_in", 900))
    interval = int(info.get("interval", 5))
    # Flushed: with stdout redirected to a file, a buffered code would sit unseen until the flow ended.
    print(f"[copilot] open {info['verification_uri']} and enter the code {info['user_code']}", flush=True)
    print(
        f"[copilot] the code is valid for about {expires_in // 60} minutes; waiting for your approval...\n", flush=True
    )

    url = os.getenv("GITHUB_COPILOT_ACCESS_TOKEN_URL", DEFAULT_GITHUB_ACCESS_TOKEN_URL)
    body = {
        "client_id": os.getenv("GITHUB_COPILOT_CLIENT_ID", DEFAULT_GITHUB_CLIENT_ID),
        "device_code": info["device_code"],
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    deadline = time.monotonic() + expires_in
    with httpx.Client(timeout=30) as client:
        while time.monotonic() < deadline:
            time.sleep(interval)
            try:
                data = client.post(url, headers=auth._get_github_headers(), json=body).json()
            except httpx.HTTPError as exc:  # the network went away between two polls
                return unreachable(exc)
            if access_token := data.get("access_token"):
                auth._ensure_token_dir()
                token.write_text(access_token, encoding="utf-8")
                try:
                    auth.get_api_key()  # exchange it once now, so a failure shows here and not in a review
                except GetAPIKeyError as exc:  # GitHub knows the account; Copilot serves it no key
                    token.unlink()  # kept, the reviewer would read it as ready and fail at their first call instead
                    print(f"[copilot] GitHub signed you in, but Copilot gave no key: {exc}", file=sys.stderr)
                    print(
                        "[copilot] usually that account has no Copilot seat; a proxy that blocks api.github.com reads "
                        "the same. Run this again with an account that has one",
                        file=sys.stderr,
                    )
                    return 1
                keep_private(token.parent)
                print(f"[copilot] done: token cached at {token}")
                return 0
            error = data.get("error")
            if error == "slow_down":
                interval += 5
            elif error != "authorization_pending":
                print(f"[copilot] GitHub answered {error!r}: {data.get('error_description', '')}", file=sys.stderr)
                return 1
    print("[copilot] the code expired before it was approved; run this again", file=sys.stderr)
    return 1


def run() -> int:
    """The process boundary of the installed `adk-review-login`."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(run())
