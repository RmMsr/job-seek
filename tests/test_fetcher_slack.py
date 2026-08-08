import json
import time
from unittest.mock import MagicMock
import httpx
import pytest
import respx
from app.fetchers.slack import SlackFetcher, _clean_mrkdwn, _ts_to_iso

_SOURCE = {
    "id": 1,
    "name": "Test Slack",
    "url": "https://example-workspace.slack.com/archives/C0EXAMPLE1",
    "fetcher_type": "slack",
}
_TARGET_URL = "https://example-workspace.slack.com/messages/C0EXAMPLE1"
_HISTORY_URL = "https://example-workspace.slack.com/api/conversations.history"
_TOKEN = "xoxc-fake-token"
_COOKIE = "xoxd-fake-cookie"


def _ts(days_ago: float) -> str:
    return str(time.time() - days_ago * 86400)


def _mock_page(local_config: dict, cookies: list[dict]):
    page = MagicMock()
    page.evaluate.return_value = json.dumps(local_config)
    page.context.cookies.return_value = cookies
    return page


def _local_config(domain="example-workspace", token=_TOKEN, team_id="T0EXAMPLE1"):
    return {"teams": {team_id: {"id": team_id, "domain": domain, "token": token}}}


def _cookies(value=_COOKIE):
    return [{"name": "d", "value": value, "domain": ".slack.com"}]


def _history_response(messages, has_more=False, next_cursor=None):
    body = {"ok": True, "messages": messages, "has_more": has_more}
    if next_cursor:
        body["response_metadata"] = {"next_cursor": next_cursor}
    return httpx.Response(200, json=body)


def _msg(ts, text, user="U_TEST"):
    return {"ts": ts, "text": text, "user": user}


def _body(job) -> str:
    """Message text with the 'Posted by <author>:' attribution prefix stripped."""
    return job.raw_text.split("\n\n", 1)[1]


# -- mrkdwn cleanup ---------------------------------------------------------


def test_clean_mrkdwn_converts_link_to_markdown_link():
    assert _clean_mrkdwn("Check <https://example.com/job|this posting>") == (
        "Check [this posting](https://example.com/job)"
    )


def test_clean_mrkdwn_converts_bare_url_to_markdown_link():
    assert _clean_mrkdwn("See <https://example.com/job>") == "See [https://example.com/job](https://example.com/job)"


def test_clean_mrkdwn_unescapes_html_entities():
    assert _clean_mrkdwn("R&amp;D role, salary &lt; 100k &gt; 50k") == "R&D role, salary < 100k > 50k"


def test_clean_mrkdwn_leaves_emoji_shortcodes_and_mentions_alone():
    assert _clean_mrkdwn("Hiring now :moneybag: cc <@U123>") == "Hiring now :moneybag: cc <@U123>"


def test_clean_mrkdwn_converts_single_asterisk_bold_to_double():
    assert _clean_mrkdwn("This is *bold* text") == "This is **bold** text"


def test_clean_mrkdwn_converts_multiple_bold_spans():
    assert _clean_mrkdwn("*Role:* Engineer, *Location:* Remote") == "**Role:** Engineer, **Location:** Remote"


def test_clean_mrkdwn_bold_spans_multiple_words():
    assert _clean_mrkdwn("*Senior ML Engineer* wanted") == "**Senior ML Engineer** wanted"


def test_clean_mrkdwn_does_not_bold_isolated_asterisk_or_multiplication():
    assert _clean_mrkdwn("3 * 4 = 12") == "3 * 4 = 12"


def test_clean_mrkdwn_does_not_bold_across_whitespace_padded_asterisks():
    # Slack itself doesn't treat "* not bold *" (space right inside the
    # asterisks) as bold, so neither should we.
    assert _clean_mrkdwn("* not bold *") == "* not bold *"


def test_clean_mrkdwn_bold_survives_inside_link_label():
    assert _clean_mrkdwn("<https://x.com|*Apply now*>") == "[**Apply now**](https://x.com)"


# -- timestamp conversion ----------------------------------------------------


def test_ts_to_iso_converts_slack_epoch_string():
    assert _ts_to_iso("0") == "1970-01-01T00:00:00+00:00"


# -- credential extraction ---------------------------------------------------


def test_extract_credentials_waits_for_local_config_to_populate():
    page = _mock_page(_local_config(), _cookies())
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    fetcher._extract_credentials(page)
    page.wait_for_function.assert_called_once_with(
        "() => !!localStorage.getItem('localConfig_v2')", timeout=15000
    )


def test_extract_credentials_finds_token_for_matching_workspace_domain():
    page = _mock_page(_local_config(), _cookies())
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    token, cookie = fetcher._extract_credentials(page)
    assert token == _TOKEN
    assert cookie == _COOKIE


def test_extract_credentials_picks_correct_team_among_several():
    config = {
        "teams": {
            "T_OTHER": {"id": "T_OTHER", "domain": "some-other-workspace", "token": "xoxc-wrong"},
            "T0EXAMPLE1": {"id": "T0EXAMPLE1", "domain": "example-workspace", "token": _TOKEN},
        }
    }
    page = _mock_page(config, _cookies())
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    token, _ = fetcher._extract_credentials(page)
    assert token == _TOKEN


def test_extract_credentials_raises_when_workspace_not_found():
    config = {"teams": {"T_OTHER": {"id": "T_OTHER", "domain": "some-other-workspace", "token": "x"}}}
    page = _mock_page(config, _cookies())
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    with pytest.raises(RuntimeError):
        fetcher._extract_credentials(page)


def test_extract_credentials_raises_when_no_session_cookie():
    page = _mock_page(_local_config(), cookies=[])
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    with pytest.raises(RuntimeError):
        fetcher._extract_credentials(page)


# -- message conversion -------------------------------------------------------


def test_message_to_job_builds_raw_job_with_ts_based_url_and_published_at():
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    job = fetcher._message_to_job(_msg("123.456", "Hiring an ML engineer", user="U123"))
    assert job.url == f"{_TARGET_URL}#123.456"
    assert job.raw_text == "Posted by U123:\n\nHiring an ML engineer"
    assert job.published_at == _ts_to_iso("123.456")


def test_message_to_job_returns_none_for_empty_text():
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    assert fetcher._message_to_job(_msg("123.456", "   ")) is None


def test_message_to_job_cleans_mrkdwn():
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    job = fetcher._message_to_job(_msg("123.456", "R&amp;D <https://x.com|apply>", user="U123"))
    assert job.raw_text == "Posted by U123:\n\nR&D [apply](https://x.com)"


def test_message_to_job_title_is_post_from_author():
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    job = fetcher._message_to_job(_msg("123.456", "some content", user="U123"))
    assert job.title == "Post from U123"


def test_message_to_job_prefers_username_over_user_id_for_bot_posts():
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    message = {"ts": "123.456", "text": "New job!", "user": "U_BOT_INTEGRATION", "username": "post-a-job"}
    job = fetcher._message_to_job(message)
    assert job.title == "Post from post-a-job"
    assert job.raw_text == "Posted by post-a-job:\n\nNew job!"


def test_message_to_job_uses_unknown_when_no_author_field():
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    message = {"ts": "123.456", "text": "some content"}
    job = fetcher._message_to_job(message)
    assert job.title == "Post from unknown"
    assert job.raw_text == "Posted by unknown:\n\nsome content"


@pytest.mark.parametrize(
    "subtype",
    [
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
    ],
)
def test_message_to_job_returns_none_for_system_subtypes(subtype):
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    message = {"ts": "123.456", "text": "This message was deleted.", "subtype": subtype}
    assert fetcher._message_to_job(message) is None


def test_message_to_job_logs_skip_reason_for_system_subtype(caplog):
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    message = {"ts": "123.456", "text": "This message was deleted.", "subtype": "tombstone"}
    with caplog.at_level("INFO", logger="job_seek"):
        fetcher._message_to_job(message)
    assert any("tombstone" in r.message and "123.456" in r.message for r in caplog.records)


def test_message_to_job_keeps_bot_message_subtype():
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    message = {"ts": "123.456", "text": "New job! Senior Engineer", "subtype": "bot_message", "user": "U123"}
    job = fetcher._message_to_job(message)
    assert job is not None
    assert _body(job) == "New job! Senior Engineer"


# -- extract / pagination -----------------------------------------------------


@respx.mock
def test_extract_returns_messages_from_a_single_page():
    page = _mock_page(_local_config(), _cookies())
    respx.post(_HISTORY_URL).mock(
        return_value=_history_response([_msg(_ts(1), "Recent post"), _msg(_ts(2), "Older post")])
    )

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    jobs = fetcher._extract(page)

    assert len(jobs) == 2
    assert _body(jobs[0]) == "Recent post"
    assert _body(jobs[1]) == "Older post"


@respx.mock
def test_extract_stops_at_messages_older_than_max_age():
    page = _mock_page(_local_config(), _cookies())
    respx.post(_HISTORY_URL).mock(
        return_value=_history_response([_msg(_ts(1), "Recent post"), _msg(_ts(40), "Ancient post")])
    )

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    jobs = fetcher._extract(page)

    assert len(jobs) == 1
    assert _body(jobs[0]) == "Recent post"


@respx.mock
def test_extract_skips_already_known_message_but_keeps_going():
    known_ts = _ts(2)
    known_url = f"{_TARGET_URL}#{known_ts}"
    page = _mock_page(_local_config(), _cookies())
    respx.post(_HISTORY_URL).mock(
        return_value=_history_response(
            [_msg(_ts(1), "New post"), _msg(known_ts, "Already seen"), _msg(_ts(3), "Older new post")]
        )
    )

    fetcher = SlackFetcher(_SOURCE, "profile-dir", known_urls=frozenset({known_url}))
    jobs = fetcher._extract(page)

    assert [_body(j) for j in jobs] == ["New post", "Older new post"]


@respx.mock
def test_extract_known_message_does_not_halt_pagination():
    """Self-healing: a page that's entirely already-known messages must not stop
    the walk — an earlier partial failure could leave unknown messages on later
    pages that still need to be picked up."""
    known_ts = _ts(1)
    known_url = f"{_TARGET_URL}#{known_ts}"
    page = _mock_page(_local_config(), _cookies())
    route = respx.post(_HISTORY_URL)
    route.side_effect = [
        _history_response([_msg(known_ts, "Already seen")], has_more=True, next_cursor="cursor-1"),
        _history_response([_msg(_ts(2), "Page two post")], has_more=False),
    ]

    fetcher = SlackFetcher(_SOURCE, "profile-dir", known_urls=frozenset({known_url}))
    jobs = fetcher._extract(page)

    assert [_body(j) for j in jobs] == ["Page two post"]
    assert route.calls.call_count == 2


@respx.mock
def test_extract_stops_once_new_message_cap_reached():
    class _CappedFetcher(SlackFetcher):
        MAX_NEW_MESSAGES = 2

    page = _mock_page(_local_config(), _cookies())
    respx.post(_HISTORY_URL).mock(
        return_value=_history_response(
            [_msg(_ts(1), "one"), _msg(_ts(1.01), "two"), _msg(_ts(1.02), "three")],
            has_more=True, next_cursor="next",
        )
    )

    fetcher = _CappedFetcher(_SOURCE, "profile-dir")
    jobs = fetcher._extract(page)

    assert [_body(j) for j in jobs] == ["one", "two"]


@respx.mock
def test_extract_follows_cursor_across_pages():
    page = _mock_page(_local_config(), _cookies())
    route = respx.post(_HISTORY_URL)
    route.side_effect = [
        _history_response([_msg(_ts(1), "Page one post")], has_more=True, next_cursor="cursor-1"),
        _history_response([_msg(_ts(2), "Page two post")], has_more=False),
    ]

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    jobs = fetcher._extract(page)

    assert [_body(j) for j in jobs] == ["Page one post", "Page two post"]
    assert route.calls.call_count == 2
    second_request_body = route.calls[1].request.content.decode()
    assert "cursor-1" in second_request_body


@respx.mock
def test_extract_stops_at_safety_cap_on_pages():
    class _CappedFetcher(SlackFetcher):
        MAX_HISTORY_PAGES = 2

    page = _mock_page(_local_config(), _cookies())
    route = respx.post(_HISTORY_URL)
    route.mock(
        return_value=_history_response([_msg(_ts(1), "post")], has_more=True, next_cursor="next")
    )

    fetcher = _CappedFetcher(_SOURCE, "profile-dir")
    fetcher._extract(page)

    assert route.calls.call_count == 2


# -- extract / diagnostic logging ---------------------------------------------


@respx.mock
def test_extract_logs_message_count_per_page(caplog):
    page = _mock_page(_local_config(), _cookies())
    respx.post(_HISTORY_URL).mock(
        return_value=_history_response([_msg(_ts(1), "Recent post"), _msg(_ts(2), "Older post")])
    )

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    with caplog.at_level("INFO", logger="job_seek"):
        fetcher._extract(page)

    assert any("2 message" in r.message for r in caplog.records)


@respx.mock
def test_extract_logs_stop_reason_and_timestamp_for_old_message(caplog):
    page = _mock_page(_local_config(), _cookies())
    old_ts = _ts(40)
    respx.post(_HISTORY_URL).mock(
        return_value=_history_response([_msg(_ts(1), "Recent post"), _msg(old_ts, "Ancient post")])
    )

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    with caplog.at_level("INFO", logger="job_seek"):
        fetcher._extract(page)

    assert any(old_ts in r.message and "older" in r.message for r in caplog.records)


@respx.mock
def test_extract_logs_stop_reason_and_url_for_already_known_message(caplog):
    known_ts = _ts(2)
    known_url = f"{_TARGET_URL}#{known_ts}"
    page = _mock_page(_local_config(), _cookies())
    respx.post(_HISTORY_URL).mock(
        return_value=_history_response([_msg(_ts(1), "New post"), _msg(known_ts, "Already seen")])
    )

    fetcher = SlackFetcher(_SOURCE, "profile-dir", known_urls=frozenset({known_url}))
    with caplog.at_level("INFO", logger="job_seek"):
        fetcher._extract(page)

    assert any(known_url in r.message and "already known" in r.message for r in caplog.records)


@respx.mock
def test_extract_logs_reaching_end_of_history(caplog):
    page = _mock_page(_local_config(), _cookies())
    respx.post(_HISTORY_URL).mock(return_value=_history_response([_msg(_ts(1), "Only post")]))

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    with caplog.at_level("INFO", logger="job_seek"):
        fetcher._extract(page)

    assert any("end of channel history" in r.message for r in caplog.records)


@respx.mock
def test_extract_logs_safety_cap_reason(caplog):
    class _CappedFetcher(SlackFetcher):
        MAX_HISTORY_PAGES = 2

    page = _mock_page(_local_config(), _cookies())
    respx.post(_HISTORY_URL).mock(
        return_value=_history_response([_msg(_ts(1), "post")], has_more=True, next_cursor="next")
    )

    fetcher = _CappedFetcher(_SOURCE, "profile-dir")
    with caplog.at_level("INFO", logger="job_seek"):
        fetcher._extract(page)

    assert any("safety cap" in r.message for r in caplog.records)


@respx.mock
def test_extract_raises_on_slack_api_error():
    page = _mock_page(_local_config(), _cookies())
    respx.post(_HISTORY_URL).mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
    )

    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    with pytest.raises(RuntimeError):
        fetcher._extract(page)


# -- login / URL handling -----------------------------------------------------


def test_needs_login_true_when_redirected_to_signin():
    page = MagicMock(url="https://app.slack.com/workspace-signin?redir=%2Fgantry%2Fauth")
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    assert fetcher._needs_login(page) is True


def test_needs_login_false_when_on_channel_page():
    page = MagicMock(url=_TARGET_URL)
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    assert fetcher._needs_login(page) is False


def test_needs_login_true_during_intermediate_sso_redirect():
    page = MagicMock(url="https://accounts.google.com/o/oauth2/v2/auth?client_id=slack")
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    assert fetcher._needs_login(page) is True


def test_needs_login_false_when_on_channel_page_with_extra_path():
    page = MagicMock(url=_TARGET_URL + "/thread/T1-123.456")
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    assert fetcher._needs_login(page) is False


def test_needs_login_true_when_channel_id_only_in_login_redirect_query():
    page = MagicMock(
        url="https://example-workspace.slack.com/?redir=%2Farchives%2FC0EXAMPLE1%3Fname%3DC0EXAMPLE1"
    )
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    assert fetcher._needs_login(page) is True


def test_needs_login_false_when_slack_canonicalizes_to_client_url():
    page = MagicMock(url="https://app.slack.com/client/T0EXAMPLE1/C0EXAMPLE1")
    fetcher = SlackFetcher(_SOURCE, "profile-dir")
    assert fetcher._needs_login(page) is False


def test_target_url_normalizes_archives_link_to_messages_link():
    source = {**_SOURCE, "url": "https://example-workspace.slack.com/archives/C0EXAMPLE1"}
    fetcher = SlackFetcher(source, "profile-dir")
    assert fetcher._target_url() == _TARGET_URL


def test_target_url_passes_through_messages_link_unchanged():
    source = {**_SOURCE, "url": _TARGET_URL}
    fetcher = SlackFetcher(source, "profile-dir")
    assert fetcher._target_url() == _TARGET_URL


def test_target_url_normalizes_trailing_slash():
    source = {**_SOURCE, "url": "https://example-workspace.slack.com/archives/C0EXAMPLE1/"}
    fetcher = SlackFetcher(source, "profile-dir")
    assert fetcher._target_url() == _TARGET_URL


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://app.slack.com/client/T0EXAMPLE1/C0EXAMPLE1",
        "https://example-workspace.slack.com/channels/C0EXAMPLE1",
        "not-a-url",
        "https://example.com/archives/C0EXAMPLE1",
    ],
)
def test_constructing_fetcher_raises_for_unsupported_url_form(bad_url):
    source = {**_SOURCE, "url": bad_url}
    with pytest.raises(ValueError):
        SlackFetcher(source, "profile-dir")
