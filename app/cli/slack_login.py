from __future__ import annotations
import argparse
import sys
import tempfile
from pathlib import Path
import httpx
from app.fetchers.slack import _resolve_target
from app.fetchers.slack_login import SlackCookieLogin

_WARNING = (
    "Heads up: this cookie is your whole Slack login. Once captured, it will\n"
    "be sent to {host} and stored there — with it, that app can read and\n"
    "post as you in every Slack workspace you're signed into, not just this\n"
    "one.\n"
)


def _confirm(host: str) -> bool:
    print(_WARNING.format(host=host))
    answer = input("Type 'yes' to continue: ")
    return answer.strip().lower() == "yes"


def _profile_dir(workspace: str, reuse: bool) -> tuple[str, tempfile.TemporaryDirectory | None]:
    if reuse:
        path = Path.home() / ".cache" / "job-seek" / "slack-login" / workspace
        path.mkdir(parents=True, exist_ok=True)
        return str(path), None
    tmp = tempfile.TemporaryDirectory()
    return tmp.name, tmp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.slack_login",
        description=(
            "Log in to Slack in a real browser and send the session cookie to a "
            "running job-seek instance."
        ),
    )
    parser.add_argument("slack_url", help="The Slack channel URL shown on the source's sources page")
    parser.add_argument(
        "cookie_post_url", help="The job-seek /sources/{id}/cookie URL to send the captured cookie to"
    )
    parser.add_argument(
        "--reuse-login-profile",
        action="store_true",
        help=(
            "Keep the browser profile on disk between runs "
            "(~/.cache/job-seek/slack-login/<workspace>) so a later run may skip "
            "re-entering credentials if Slack still remembers the browser. Off by "
            "default to keep no state on disk after the tool exits -- opt in to "
            "evaluate whether the usability gain is worth it."
        ),
    )
    args = parser.parse_args(argv)

    host = httpx.URL(args.cookie_post_url).host
    if not _confirm(host):
        print("Aborted: confirmation not received.")
        return 1

    _, workspace, _ = _resolve_target(args.slack_url)
    profile_dir, tmp = _profile_dir(workspace, args.reuse_login_profile)
    try:
        source = {"name": workspace, "url": args.slack_url}
        login = SlackCookieLogin(source, profile_dir)
        cookie = None
        gen = login.login()
        try:
            while True:
                print(next(gen))
        except StopIteration as stop:
            cookie = stop.value
    finally:
        if tmp is not None:
            tmp.cleanup()

    if not cookie:
        print("Login did not produce a session cookie; nothing was sent.")
        return 1

    try:
        resp = httpx.post(args.cookie_post_url, data={"d_cookie": cookie, "acknowledged": "on"})
    except httpx.HTTPError as exc:
        print(f"Could not reach {args.cookie_post_url}: {exc}")
        return 1

    if 200 <= resp.status_code < 300:
        print(f"Cookie sent ({resp.status_code}). Reload the sources page to see the updated status.")
        return 0
    print(f"Server rejected the cookie ({resp.status_code}): {resp.text}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
