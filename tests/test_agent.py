"""Pytest suite for agent.py.

Covers the validation layer that sits between the LLM's JSON response
and downstream Python — specifically `_filter_events_by_source_id`,
which enforces that each extracted event carries a `source_message_id`
that maps back to one of the emails in the current batch.

That mapping is what lets main.py look up the original sender domain
for the Ignore-sender feature. If the LLM omits the field or invents an
ID, the event is dropped with a warning (tolerant-parse posture — see
design/failure-notifications.md).
"""
from __future__ import annotations

import sys
from pathlib import Path

# agent.py lives at the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import agent  # noqa: E402


def _event(name: str, sid: str | None) -> dict:
    """Minimal event dict for filter-path tests.

    Only the source_message_id key matters for these tests; name is a
    human-readable label so failure output is legible. If sid is None
    the key is omitted entirely (models that ignore the schema).
    """
    ev: dict = {"name": name, "date": "2026-05-01"}
    if sid is not None:
        ev["source_message_id"] = sid
    return ev


# ─── valid source_message_id ──────────────────────────────────────────────


def test_keeps_events_whose_source_id_is_in_batch():
    batch_ids = {"1111aaaa2222bbbb", "3333cccc4444dddd"}
    events = [
        _event("A", "1111aaaa2222bbbb"),
        _event("B", "3333cccc4444dddd"),
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert [e["name"] for e in kept] == ["A", "B"]


def test_preserves_event_order_and_extra_keys():
    batch_ids = {"1111aaaa2222bbbb"}
    events = [
        {
            "name": "E1",
            "date": "2026-05-01",
            "time": "6:30 PM",
            "source_message_id": "1111aaaa2222bbbb",
        }
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert kept == events


# ─── missing source_message_id ────────────────────────────────────────────


def test_drops_event_with_missing_source_id(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [
        _event("keep", "1111aaaa2222bbbb"),
        _event("missing", None),
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert [e["name"] for e in kept] == ["keep"]
    out = capsys.readouterr().out
    assert "missing source_message_id" in out
    assert "dropped 1 event" in out


def test_drops_event_with_empty_source_id(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [_event("empty", "")]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert kept == []
    assert "missing source_message_id" in capsys.readouterr().out


def test_drops_event_with_non_string_source_id(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [_event("wrong-type", 12345)]  # type: ignore[arg-type]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert kept == []
    assert "missing source_message_id" in capsys.readouterr().out


# ─── hallucinated / unknown source_message_id ─────────────────────────────


def test_drops_event_whose_source_id_is_not_in_batch(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [
        _event("keep", "1111aaaa2222bbbb"),
        _event("hallucinated", "9999ffff0000eeee"),
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert [e["name"] for e in kept] == ["keep"]
    out = capsys.readouterr().out
    assert "did not match any email in the batch" in out
    assert "dropped 1 event" in out


def test_warning_counts_are_separate_for_missing_and_unknown(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [
        _event("missing", None),
        _event("hallucinated", "deadbeefdeadbeef"),
        _event("keep", "1111aaaa2222bbbb"),
    ]
    kept = agent._filter_events_by_source_id(events, batch_ids)
    assert [e["name"] for e in kept] == ["keep"]
    out = capsys.readouterr().out
    # Both warning branches fire independently so a batch can surface
    # a mix of schema violations and hallucinations in one pass.
    assert "missing source_message_id" in out
    assert "did not match any email in the batch" in out


# ─── empty / no-op cases ──────────────────────────────────────────────────


def test_empty_events_list_is_noop():
    assert agent._filter_events_by_source_id([], {"1111aaaa2222bbbb"}) == []


def test_no_warnings_when_nothing_is_dropped(capsys):
    batch_ids = {"1111aaaa2222bbbb"}
    events = [_event("ok", "1111aaaa2222bbbb")]
    agent._filter_events_by_source_id(events, batch_ids)
    assert capsys.readouterr().out == ""


# ─── teacher roster injection (#12 subtask) ───────────────────────────────


def test_extraction_prompt_embeds_roster_prose():
    """End-to-end: the fully-formed prompt carries kid names, grades,
    teachers, school, activity providers, and the attribution rules.
    Guards the module-load wiring — if the roster loader or the
    activity-clause branch ever breaks silently, this fails."""
    prompt = agent.EXTRACTION_SYSTEM_PROMPT
    for needle in (
        "Teacher roster",
        "Everly",
        "Isla",
        "Ms. Anita Sahai",
        "Ms. Meredith Rohde",
        "Louise Archer Elementary",
        "6th",
        "3rd",
        "attribute",
        "Born 2 Dance Studio (B2D)",
        "Cuppett Performing Arts Center",
        "activity provider",
    ):
        assert needle in prompt, f"missing from prompt: {needle!r}"


def test_format_roster_prose_shape():
    """Unit test on the pure formatter. No filesystem. Fabricated mapping
    with a third kid proves the loop scales and nothing in the formatter
    hard-codes Everly/Isla. No `activities` key — exercises the no-clause
    branch and asserts the kid's line stops at the teacher."""
    mapping = {
        "Alice": {"teacher": "Mr. Smith", "grade": "4th", "school": "Oakwood"},
        "Bob":   {"teacher": "Ms. Jones", "grade": "2nd", "school": "Oakwood"},
    }
    prose = agent._format_roster_prose(mapping)
    assert prose.startswith("Teacher roster (current academic year):")
    assert "- Alice is in 4th grade at Oakwood, taught by Mr. Smith." in prose
    assert "- Bob is in 2nd grade at Oakwood, taught by Ms. Jones." in prose
    assert "activities:" not in prose  # no kid has activities in this fixture
    assert "attribute" in prose


def test_format_roster_prose_includes_activities_clause():
    """Activities are optional per-kid and surface as a semicolon clause
    on the kid's line. Mixed fixture: one kid has one activity, one has
    two, one has none. Asserts all three rendering paths are stable."""
    mapping = {
        "Alice": {
            "teacher": "Mr. Smith",
            "grade": "4th",
            "school": "Oakwood",
            "activities": ["Ridgeline Swim Club"],
        },
        "Bob": {
            "teacher": "Ms. Jones",
            "grade": "2nd",
            "school": "Oakwood",
            "activities": ["Junior Chess League", "Wren Nature Center"],
        },
        "Carla": {
            "teacher": "Ms. Rivera",
            "grade": "1st",
            "school": "Oakwood",
            "activities": [],  # explicitly empty — no clause, no trailing dangler
        },
    }
    prose = agent._format_roster_prose(mapping)
    assert (
        "- Alice is in 4th grade at Oakwood, taught by Mr. Smith; "
        "activities: Ridgeline Swim Club." in prose
    )
    assert (
        "- Bob is in 2nd grade at Oakwood, taught by Ms. Jones; "
        "activities: Junior Chess League, Wren Nature Center." in prose
    )
    assert "- Carla is in 1st grade at Oakwood, taught by Ms. Rivera." in prose
    # The activity-routing sentence is appended whenever this formatter
    # runs, even if a particular fixture has no populated activities;
    # the rule is cheap and harmless, and keeping it unconditional means
    # one less branch in production.
    assert "activity provider" in prose


def test_load_roster_prose_raises_on_missing_file(tmp_path):
    """Missing roster file must raise at load time — not paper over with a
    silent fallback. The file is committed and its absence is a bug."""
    missing = tmp_path / "nope.json"
    import pytest
    with pytest.raises(FileNotFoundError):
        agent._load_roster_prose(missing)


# ─── newsletter-isolated batching (#17 subtask) ───────────────────────────
#
# `_sender_key` collapses the `From` header to the same lowercased-mailbox
# form `newsletter_stats.py` stores; `_plan_batches` uses it to partition
# emails into newsletter (size-1) and regular (BATCH_SIZE) batches.
# Newsletter batches run FIRST — if an API call fails midway, the
# high-yield messages are already extracted.


def _mk_email(mid: str, sender: str) -> dict:
    """Minimal email dict for batch-planning tests.

    Only messageId and from_ matter; the rest of the fields the agent
    normally threads through the prompt are irrelevant for the
    partition/chunk logic covered here.
    """
    return {
        "messageId": mid,
        "from_": sender,
        "subject": "",
        "date_sent": "",
        "body": "",
    }


def test_sender_key_bare_address():
    assert agent._sender_key("foo@bar.com") == "foo@bar.com"


def test_sender_key_named_form():
    assert agent._sender_key("Foo Bar <foo@bar.com>") == "foo@bar.com"


def test_sender_key_lowercases_mixed_case():
    """Case folding happens at key time so the stats file can store one
    canonical form and the batching lookup stays case-insensitive."""
    assert agent._sender_key("FOO@BAR.COM") == "foo@bar.com"


def test_sender_key_empty_and_missing_headers_return_empty():
    """Empty / None headers collapse to an empty-string key. Callers
    treat empty-key as 'not in the newsletter set' — the safe default
    (batched at BATCH_SIZE)."""
    assert agent._sender_key("") == ""
    assert agent._sender_key(None) == ""  # type: ignore[arg-type]


def test_sender_key_garbage_header_never_matches_a_real_mailbox():
    """`email.utils.parseaddr` is tolerant: on garbage input without
    angle brackets it may return the first bare token ('not' for
    'not an email'). That's fine — any such token can't equal a real
    lowercased mailbox in the newsletter set, so the email safely flows
    into the regular partition. This test pins the safety property
    rather than the exact parseaddr return value."""
    key = agent._sender_key("not an email")
    # The key may be empty or a bare token; crucially, it is never
    # equal to a realistic mailbox, so newsletter matching is safe.
    assert "@" not in key


def test_plan_batches_default_matches_prior_chunking():
    """Regression guard. None preserves the pre-#17 behavior: a single
    partition chunked at BATCH_SIZE, in input order."""
    emails = [_mk_email(f"m{i}", f"s{i}@x.com") for i in range(25)]
    batches = agent._plan_batches(emails, None)
    # 25 emails → ceil(25/10) = 3 batches of sizes 10, 10, 5
    assert [len(b) for b in batches] == [10, 10, 5]
    # Input order preserved across the flattened batches
    flat = [e["messageId"] for b in batches for e in b]
    assert flat == [f"m{i}" for i in range(25)]


def test_plan_batches_empty_list_with_none():
    assert agent._plan_batches([], None) == []


def test_plan_batches_empty_list_with_set():
    """An empty input list produces no batches regardless of whether
    newsletter_senders is provided."""
    assert agent._plan_batches([], {"news@x.com"}) == []


def test_plan_batches_newsletter_only_runs_as_batches_of_one():
    emails = [_mk_email("m1", "news@x.com"), _mk_email("m2", "news@x.com")]
    batches = agent._plan_batches(emails, {"news@x.com"})
    assert [len(b) for b in batches] == [1, 1]
    assert batches[0][0]["messageId"] == "m1"
    assert batches[1][0]["messageId"] == "m2"


def test_plan_batches_regular_only_chunks_at_batch_size():
    """Empty newsletter set means every email is regular; resulting
    chunking should be identical to the `None` default."""
    emails = [_mk_email(f"m{i}", "regular@x.com") for i in range(12)]
    batches = agent._plan_batches(emails, set())
    assert [len(b) for b in batches] == [10, 2]


def test_plan_batches_newsletters_first_then_regulars():
    """Mixed partition: newsletters (batch-of-1) ordered BEFORE regulars
    (BATCH_SIZE chunk). Order within each partition is preserved."""
    emails = [
        _mk_email("r1", "regular@x.com"),
        _mk_email("n1", "news@x.com"),
        _mk_email("r2", "regular@x.com"),
        _mk_email("n2", "news@x.com"),
        _mk_email("r3", "regular@x.com"),
    ]
    batches = agent._plan_batches(emails, {"news@x.com"})
    # 2 newsletter batches (size 1 each) first, then 1 regular batch (size 3)
    assert [len(b) for b in batches] == [1, 1, 3]
    assert batches[0][0]["messageId"] == "n1"
    assert batches[1][0]["messageId"] == "n2"
    assert [e["messageId"] for e in batches[2]] == ["r1", "r2", "r3"]


def test_plan_batches_newsletter_heavy_run_plus_multiple_regular_batches():
    """15 regulars + 3 newsletters → 3 newsletter batches first, then
    2 regular batches (10+5). Proves both partitions chunk correctly."""
    emails = [_mk_email(f"n{i}", "news@x.com") for i in range(3)]
    emails += [_mk_email(f"r{i}", "regular@x.com") for i in range(15)]
    batches = agent._plan_batches(emails, {"news@x.com"})
    assert [len(b) for b in batches] == [1, 1, 1, 10, 5]
    for i in range(3):
        assert batches[i][0]["messageId"] == f"n{i}"


def test_plan_batches_sender_match_is_case_insensitive():
    """From-header senders often arrive in mixed case. _sender_key
    lowercases before lookup, so the stats-file canonical form (lower)
    still matches."""
    emails = [_mk_email("m1", "News@X.COM")]
    batches = agent._plan_batches(emails, {"news@x.com"})
    assert [len(b) for b in batches] == [1]


def test_plan_batches_uses_named_form_mailbox_for_match():
    """Senders often come through as `"Foo Bar <foo@bar.com>"`. The key
    is the inner mailbox, so the newsletter set lookup must hit."""
    emails = [_mk_email("m1", "PTA Sunbeam <sunbeam@laespta.org>")]
    batches = agent._plan_batches(emails, {"sunbeam@laespta.org"})
    assert [len(b) for b in batches] == [1]


def test_plan_batches_unknown_sender_is_regular():
    """A sender not in the newsletter set rides the regular partition
    even when `newsletter_senders` is non-empty."""
    emails = [_mk_email("m1", "surprise@x.com")]
    batches = agent._plan_batches(emails, {"news@x.com"})
    # Single email → one regular batch of size 1 (not forced batch-of-1
    # because that only applies to matched newsletters; here it is just
    # the last-chunk residual of a BATCH_SIZE chunking)
    assert [len(b) for b in batches] == [1]
    assert batches[0][0]["messageId"] == "m1"


def test_plan_batches_missing_from_header_is_regular():
    """Email dicts without a `from_` key (defensive: should never
    happen, but protect the partition loop from KeyError) flow into
    the regular bucket via the empty-string key default."""
    emails = [{"messageId": "m1", "subject": "", "date_sent": "", "body": ""}]
    batches = agent._plan_batches(emails, {"news@x.com"})
    assert [len(b) for b in batches] == [1]


def test_extract_events_accepts_newsletter_senders_kwarg():
    """Guardrail: the public signature accepts the new kwarg with a
    default of None. Verified without an API call by inspecting the
    signature so future refactors can't silently drop the kwarg."""
    import inspect
    sig = inspect.signature(agent.extract_events)
    assert "newsletter_senders" in sig.parameters
    assert sig.parameters["newsletter_senders"].default is None


# ─── _parse_json_response ─────────────────────────────────────────────────
#
# The model's JSON output is the boundary between LLM judgment and
# deterministic Python; this parser is the safety net that turns
# almost-right text into a usable dict (or None). Each test below pins
# one tolerated deviation — markdown fences, legacy bare-list shape,
# trailing commentary, malformed value types — so a future refactor
# can't silently drop the recovery paths.

def test_parse_json_response_dict_with_events_and_senders():
    text = '{"events": [{"name": "x"}], "irrelevant_senders": ["spam@ex.com"]}'
    result = agent._parse_json_response(text)
    assert result == {
        "events": [{"name": "x"}],
        "irrelevant_senders": ["spam@ex.com"],
    }


def test_parse_json_response_strips_markdown_code_fences_with_lang():
    text = '```json\n{"events": [], "irrelevant_senders": []}\n```'
    assert agent._parse_json_response(text) == {
        "events": [],
        "irrelevant_senders": [],
    }


def test_parse_json_response_strips_bare_code_fences():
    text = '```\n{"events": [{"name": "x"}], "irrelevant_senders": []}\n```'
    assert agent._parse_json_response(text) == {
        "events": [{"name": "x"}],
        "irrelevant_senders": [],
    }


def test_parse_json_response_legacy_bare_list_treated_as_events_only():
    text = '[{"name": "x"}, {"name": "y"}]'
    assert agent._parse_json_response(text) == {
        "events": [{"name": "x"}, {"name": "y"}],
        "irrelevant_senders": [],
    }


def test_parse_json_response_recovers_via_raw_decode_with_trailing_garbage(capsys):
    """raw_decode parses the first valid JSON value and ignores the
    rest — covers the failure mode where the model emits a valid
    response followed by chatty commentary."""
    text = '{"events": [], "irrelevant_senders": []} and then some commentary'
    result = agent._parse_json_response(text)
    assert result == {"events": [], "irrelevant_senders": []}
    out = capsys.readouterr().out
    assert "raw_decode" in out
    assert "trailing" in out


def test_parse_json_response_unrecoverable_garbage_returns_none(capsys):
    result = agent._parse_json_response("not json at all { broken")
    assert result is None
    assert "PARSE ERROR" in capsys.readouterr().out


def test_parse_json_response_events_non_list_coerced_to_empty(capsys):
    text = '{"events": "not a list", "irrelevant_senders": []}'
    result = agent._parse_json_response(text)
    assert result == {"events": [], "irrelevant_senders": []}
    assert "'events' is not a list" in capsys.readouterr().out


def test_parse_json_response_senders_non_list_coerced_to_empty(capsys):
    text = '{"events": [], "irrelevant_senders": "nope"}'
    result = agent._parse_json_response(text)
    assert result == {"events": [], "irrelevant_senders": []}
    assert "'irrelevant_senders' is not a list" in capsys.readouterr().out


def test_parse_json_response_missing_keys_default_to_empty():
    assert agent._parse_json_response("{}") == {
        "events": [],
        "irrelevant_senders": [],
    }


def test_parse_json_response_non_dict_non_list_returns_none(capsys):
    result = agent._parse_json_response("42")
    assert result is None
    assert "not a dict or list" in capsys.readouterr().out


# ─── _call_with_retry ─────────────────────────────────────────────────────
#
# Wraps client.messages.create with exponential backoff on transient API
# failures. Tests use a fake client (no network) and patch time.sleep so
# the suite stays fast. Anthropic's exception classes override __new__
# with HTTP-fixture requirements we don't have, so we bypass __init__
# via cls.__new__(cls) + Exception.__init__.

import anthropic  # noqa: E402
import pytest  # noqa: E402


def _make_anthropic_error(cls: type, *, status_code: int | None = None) -> Exception:
    err = cls.__new__(cls)
    Exception.__init__(err, f"fake {cls.__name__}")
    if status_code is not None:
        err.status_code = status_code
    return err


class _FakeMessages:
    """Replays a queue of side effects for client.messages.create.

    Each entry is either a return value (yielded as-is) or an Exception
    instance (raised). Records every call's kwargs for assertions.
    """
    def __init__(self, side_effects: list):
        self.calls: list[dict] = []
        self._effects = list(side_effects)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        effect = self._effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect


class _FakeClient:
    def __init__(self, side_effects: list):
        self.messages = _FakeMessages(side_effects)


@pytest.fixture
def no_sleep(monkeypatch):
    """Backoff sleeps would make the retry tests take ~30s; stub them out."""
    monkeypatch.setattr(agent.time, "sleep", lambda _seconds: None)


def test_call_with_retry_returns_response_on_first_try(no_sleep):
    sentinel = object()
    client = _FakeClient([sentinel])
    result = agent._call_with_retry(
        client, model="m", max_tokens=100, user_message="u", batch_label="b",
    )
    assert result is sentinel
    assert len(client.messages.calls) == 1


def test_call_with_retry_retries_on_rate_limit_then_succeeds(no_sleep, capsys):
    sentinel = object()
    err = _make_anthropic_error(anthropic.RateLimitError)
    client = _FakeClient([err, sentinel])
    result = agent._call_with_retry(
        client, model="m", max_tokens=100, user_message="u", batch_label="batch1",
    )
    assert result is sentinel
    assert len(client.messages.calls) == 2
    out = capsys.readouterr().out
    assert "batch1 attempt 1 failed" in out
    assert "RateLimitError" in out


@pytest.mark.parametrize("exc_cls", [
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
])
def test_call_with_retry_retries_on_other_transient_errors(no_sleep, exc_cls):
    sentinel = object()
    client = _FakeClient([_make_anthropic_error(exc_cls), sentinel])
    result = agent._call_with_retry(
        client, model="m", max_tokens=100, user_message="u", batch_label="b",
    )
    assert result is sentinel
    assert len(client.messages.calls) == 2


def test_call_with_retry_retries_on_api_status_529_overloaded(no_sleep, capsys):
    sentinel = object()
    err = _make_anthropic_error(anthropic.APIStatusError, status_code=529)
    client = _FakeClient([err, sentinel])
    result = agent._call_with_retry(
        client, model="m", max_tokens=100, user_message="u", batch_label="b",
    )
    assert result is sentinel
    assert "overloaded 529" in capsys.readouterr().out


def test_call_with_retry_does_not_retry_on_api_status_400(no_sleep):
    """400 is a client error; retrying would be wrong."""
    err = _make_anthropic_error(anthropic.APIStatusError, status_code=400)
    client = _FakeClient([err])
    with pytest.raises(anthropic.APIStatusError):
        agent._call_with_retry(
            client, model="m", max_tokens=100, user_message="u", batch_label="b",
        )
    assert len(client.messages.calls) == 1


def test_call_with_retry_reraises_after_exhausting_max_retries(no_sleep, capsys):
    errs = [_make_anthropic_error(anthropic.RateLimitError)
            for _ in range(agent.MAX_RETRIES)]
    client = _FakeClient(errs)
    with pytest.raises(anthropic.RateLimitError):
        agent._call_with_retry(
            client, model="m", max_tokens=100, user_message="u", batch_label="b",
        )
    assert len(client.messages.calls) == agent.MAX_RETRIES
    assert "FAILED after" in capsys.readouterr().out


def test_call_with_retry_uses_default_extraction_system_prompt(no_sleep):
    client = _FakeClient([object()])
    agent._call_with_retry(
        client, model="m", max_tokens=100, user_message="u", batch_label="b",
    )
    assert client.messages.calls[0]["system"] == agent.EXTRACTION_SYSTEM_PROMPT


def test_call_with_retry_uses_provided_system_prompt(no_sleep):
    client = _FakeClient([object()])
    agent._call_with_retry(
        client, model="m", max_tokens=100, user_message="u", batch_label="b",
        system_prompt="custom-prompt",
    )
    assert client.messages.calls[0]["system"] == "custom-prompt"


# ─── extract_events ───────────────────────────────────────────────────────
#
# End-to-end tests for the orchestrator that wires _plan_batches →
# _call_with_retry → _parse_json_response → _filter_events_by_source_id.
# Each helper has its own unit tests above; these focus on the wiring:
# what the function returns, when it fires the repair retry, when it
# skips a batch, how it aggregates across batches.
#
# Both the API client (_get_client) and the API call (_call_with_retry)
# are stubbed via monkeypatch — no network, no API key required. Fake
# responses use SimpleNamespace to mimic the .content[0].text and
# .usage.{input,output}_tokens shape that extract_events reads.

from types import SimpleNamespace  # noqa: E402


def _make_response(text: str,
                   input_tokens: int = 100,
                   output_tokens: int = 50) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


def _email(message_id: str,
           from_: str = "sender@example.com",
           subject: str = "Subject",
           body: str = "Body") -> dict:
    return {
        "messageId": message_id,
        "from_": from_,
        "date_sent": "Mon, 14 Apr 2026 08:00:00 -0400",
        "subject": subject,
        "body": body,
    }


@pytest.fixture
def stub_client(monkeypatch):
    """Replace _get_client so tests don't need a real ANTHROPIC_API_KEY."""
    monkeypatch.setattr(agent, "_get_client", lambda: object())


def test_extract_events_empty_input_returns_empty_no_api_call(monkeypatch):
    """Short-circuit before any client setup or API call."""
    called = []
    monkeypatch.setattr(agent, "_get_client",
                        lambda: called.append("client") or object())
    monkeypatch.setattr(agent, "_call_with_retry",
                        lambda *a, **k: called.append("call") or None)
    events, irrelevant = agent.extract_events([])
    assert events == []
    assert irrelevant == []
    assert called == []  # neither was invoked


def test_extract_events_happy_path_single_batch(monkeypatch, stub_client):
    response_text = (
        '{"events": [{"name": "Concert", "date": "2026-05-01", '
        '"source_message_id": "m1"}], '
        '"irrelevant_senders": [{"from": "spam@x.com", "reason": "newsletter"}]}'
    )
    monkeypatch.setattr(agent, "_call_with_retry",
                        lambda *a, **k: _make_response(response_text))
    events, irrelevant = agent.extract_events([_email("m1")])
    assert events == [{"name": "Concert", "date": "2026-05-01",
                       "source_message_id": "m1"}]
    assert irrelevant == [{"from": "spam@x.com", "reason": "newsletter"}]


def test_extract_events_drops_event_with_unknown_source_id(
    monkeypatch, stub_client, capsys,
):
    """End-to-end check that _filter_events_by_source_id is wired in:
    an event whose source_message_id isn't in the batch is dropped."""
    response_text = (
        '{"events": ['
        '{"name": "Bad", "source_message_id": "ghost"}, '
        '{"name": "Good", "source_message_id": "m1"}'
        '], "irrelevant_senders": []}'
    )
    monkeypatch.setattr(agent, "_call_with_retry",
                        lambda *a, **k: _make_response(response_text))
    events, _ = agent.extract_events([_email("m1")])
    assert [e["name"] for e in events] == ["Good"]


def test_extract_events_repair_succeeds_after_initial_parse_failure(
    monkeypatch, stub_client, capsys,
):
    responses = [
        _make_response("not json garbage"),
        _make_response(
            '{"events": [{"name": "Recovered", "source_message_id": "m1"}], '
            '"irrelevant_senders": []}'
        ),
    ]
    call_count = [0]

    def fake_call(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        return responses[idx]

    monkeypatch.setattr(agent, "_call_with_retry", fake_call)
    events, _ = agent.extract_events([_email("m1")])
    assert [e["name"] for e in events] == ["Recovered"]
    assert call_count[0] == 2  # initial + repair
    assert "Repair succeeded" in capsys.readouterr().out


def test_extract_events_repair_also_fails_skips_batch(
    monkeypatch, stub_client, capsys,
):
    """Both calls return unparseable text — skip the batch rather than
    crashing the run, so other batches can still produce events."""
    monkeypatch.setattr(agent, "_call_with_retry",
                        lambda *a, **k: _make_response("still not json"))
    events, irrelevant = agent.extract_events([_email("m1")])
    assert events == []
    assert irrelevant == []
    assert "SKIPPING" in capsys.readouterr().out


def test_extract_events_repair_call_raises_skips_batch(
    monkeypatch, stub_client, capsys,
):
    """If the repair _call_with_retry raises (e.g. exhausts its own
    retries on a transient error), the surrounding try/except prints
    and the batch is skipped rather than crashing."""
    call_count = [0]

    def fake_call(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_response("not json")
        raise RuntimeError("repair blew up")

    monkeypatch.setattr(agent, "_call_with_retry", fake_call)
    events, _ = agent.extract_events([_email("m1")])
    assert events == []
    out = capsys.readouterr().out
    assert "Repair also failed" in out
    assert "SKIPPING" in out


def test_extract_events_aggregates_across_multiple_batches(monkeypatch, stub_client):
    """BATCH_SIZE+1 emails → 2 batches; results from each must end up
    in the aggregated output."""
    responses = [
        _make_response(
            '{"events": [{"name": "A", "source_message_id": "m1"}], '
            '"irrelevant_senders": [{"from": "x@x.com", "reason": "ad"}]}'
        ),
        _make_response(
            f'{{"events": [{{"name": "B", "source_message_id": '
            f'"m{agent.BATCH_SIZE + 1}"}}], "irrelevant_senders": []}}'
        ),
    ]
    call_count = [0]

    def fake_call(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        return responses[idx]

    monkeypatch.setattr(agent, "_call_with_retry", fake_call)
    emails = [_email(f"m{i}") for i in range(1, agent.BATCH_SIZE + 2)]
    events, irrelevant = agent.extract_events(emails)
    assert sorted(e["name"] for e in events) == ["A", "B"]
    assert irrelevant == [{"from": "x@x.com", "reason": "ad"}]
    assert call_count[0] == 2
