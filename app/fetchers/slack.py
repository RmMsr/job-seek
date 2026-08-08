from __future__ import annotations
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse
import httpx
from app.fetchers.playwright_base import PlaywrightFetcher
from app.fetchers.base import RawJob, is_recent

logger = logging.getLogger("job_seek")

_URL_RE = re.compile(r"^https://([a-zA-Z0-9-]+)\.slack\.com/(?:archives|messages)/([A-Za-z0-9]+)/?$")
_MRKDWN_LINK_RE = re.compile(r"<(https?://[^|>]+)\|([^>]+)>")
_MRKDWN_BARE_URL_RE = re.compile(r"<(https?://[^>]+)>")
# Slack bold is a single asterisk with no space inside it (Slack itself
# doesn't treat "* padded *" as bold); CommonMark bold needs a doubled one.
_MRKDWN_BOLD_RE = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")

MAX_AGE_DAYS = 30  # matches app/fetchers/finn.py's cutoff

# Slack channel-management events, never job content — Slack still returns
# them from conversations.history, with a fixed placeholder text
# ("This message was deleted." for tombstones, etc.) rather than anything
# an LLM could usefully classify.
_SYSTEM_SUBTYPES = frozenset({
    "tombstone",
    "channel_name",
    "channel_topic",
    "channel_purpose",
    "channel_join",
    "channel_leave",
    "channel_archive",
    "channel_unarchive",
    "pinned_item",
    "unpinned_item",
})


def _resolve_target(source_url: str) -> tuple[str, str, str]:
    """Returns (target_url, workspace_subdomain, channel_id)."""
    match = _URL_RE.match(source_url)
    if not match:
        raise ValueError(
            f"Unsupported Slack source URL {source_url!r}: expected "
            "https://<workspace>.slack.com/archives/<channel> or .../messages/<channel>"
        )
    subdomain, channel_id = match.groups()
    return f"https://{subdomain}.slack.com/messages/{channel_id}", subdomain, channel_id


def _ts_to_iso(ts: str) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _clean_mrkdwn(text: str) -> str:
    text = _MRKDWN_LINK_RE.sub(lambda m: f"[{m.group(2)}]({m.group(1)})", text)
    text = _MRKDWN_BARE_URL_RE.sub(lambda m: f"[{m.group(1)}]({m.group(1)})", text)
    text = _MRKDWN_BOLD_RE.sub(lambda m: f"**{m.group(1)}**", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text


class SlackFetcher(PlaywrightFetcher):
    MAX_HISTORY_PAGES = 20
    PAGE_LIMIT = 100
    MAX_NEW_MESSAGES = 50  # cap on new postings collected per run, matches finn.py's MAX_DETAIL_FETCHES

    def __init__(self, source: dict, profile_dir: str, known_urls: frozenset[str] = frozenset()) -> None:
        super().__init__(source, profile_dir)
        self._resolved_url, self._workspace, self._channel_id_value = _resolve_target(source["url"])
        self._known_urls = known_urls

    def _target_url(self) -> str:
        return self._resolved_url

    def _channel_id(self) -> str:
        return self._channel_id_value

    def _needs_login(self, page) -> bool:
        return self._channel_id() not in urlparse(page.url).path

    def _extract_credentials(self, page) -> tuple[str, str]:
        page.wait_for_function("() => !!localStorage.getItem('localConfig_v2')", timeout=15000)
        raw_config = page.evaluate("() => localStorage.getItem('localConfig_v2')")
        config = json.loads(raw_config or "{}")
        team = next(
            (t for t in config.get("teams", {}).values() if t.get("domain") == self._workspace),
            None,
        )
        if team is None:
            raise RuntimeError(f"No Slack session found for workspace {self._workspace!r}")
        cookie = next((c["value"] for c in page.context.cookies() if c["name"] == "d"), None)
        if cookie is None:
            raise RuntimeError("No Slack session cookie ('d') found")
        return team["token"], cookie

    def _fetch_history_page(self, token: str, cookie: str, cursor: str | None) -> dict:
        data = {"token": token, "channel": self._channel_id(), "limit": str(self.PAGE_LIMIT)}
        if cursor:
            data["cursor"] = cursor
        resp = httpx.post(
            f"https://{self._workspace}.slack.com/api/conversations.history",
            data=data,
            cookies={"d": cookie},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            raise RuntimeError(f"Slack API error: {body.get('error')}")
        return body

    def _message_to_job(self, message: dict) -> RawJob | None:
        subtype = message.get("subtype")
        if subtype in _SYSTEM_SUBTYPES:
            logger.info(
                "Slack extract for '%s': skipping %s message (ts=%s)",
                self._source["name"], subtype, message.get("ts"),
            )
            return None
        text = _clean_mrkdwn(message.get("text", "")).strip()
        if not text:
            return None
        ts = message["ts"]
        author = message.get("username") or message.get("user") or "unknown"
        return RawJob(
            url=f"{self._target_url()}#{ts}",
            title=f"Post from {author}",
            company="",
            raw_text=f"Posted by {author}:\n\n{text}",
            published_at=_ts_to_iso(ts),
        )

    def _extract(self, page) -> list[RawJob]:
        token, cookie = self._extract_credentials(page)
        jobs: list[RawJob] = []
        cursor = None
        for page_num in range(1, self.MAX_HISTORY_PAGES + 1):
            body = self._fetch_history_page(token, cookie, cursor)
            messages = body.get("messages", [])
            logger.info(
                "Slack extract for '%s': page %d returned %d message(s)",
                self._source["name"], page_num, len(messages),
            )
            stop = False
            for message in messages:
                job = self._message_to_job(message)
                if job is None:
                    continue
                if not is_recent(job.published_at, MAX_AGE_DAYS):
                    logger.info(
                        "Slack extract for '%s': stopping, message older than %d day cutoff "
                        "(published_at=%s, url=%s)",
                        self._source["name"], MAX_AGE_DAYS, job.published_at, job.url,
                    )
                    stop = True
                    break
                if job.url in self._known_urls:
                    logger.info(
                        "Slack extract for '%s': skipping message already known "
                        "(published_at=%s, url=%s)",
                        self._source["name"], job.published_at, job.url,
                    )
                    continue
                jobs.append(job)
                if len(jobs) >= self.MAX_NEW_MESSAGES:
                    logger.info(
                        "Slack extract for '%s': stopping, reached cap of %d new message(s)",
                        self._source["name"], self.MAX_NEW_MESSAGES,
                    )
                    stop = True
                    break
            if stop:
                break
            if not body.get("has_more"):
                logger.info("Slack extract for '%s': reached end of channel history", self._source["name"])
                break
            cursor = body.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                logger.info(
                    "Slack extract for '%s': stopping, no next_cursor despite has_more",
                    self._source["name"],
                )
                break
        else:
            logger.info(
                "Slack extract for '%s': stopped at safety cap of %d page(s)",
                self._source["name"], self.MAX_HISTORY_PAGES,
            )
        return jobs
