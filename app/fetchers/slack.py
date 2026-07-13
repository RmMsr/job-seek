from __future__ import annotations
from app.fetchers.playwright_base import PlaywrightFetcher
from app.fetchers.base import RawJob


class SlackFetcher(PlaywrightFetcher):
    def _extract(self, page) -> list[RawJob]:
        page.keyboard.press("End")
        page.wait_for_timeout(2000)
        messages = page.query_selector_all("[data-qa='message_content']")
        jobs = []
        for msg in messages:
            text = msg.inner_text().strip()
            if not text:
                continue
            jobs.append(
                RawJob(
                    url=self._source["url"],
                    title="",
                    company="",
                    raw_text=text,
                )
            )
        return jobs
