"""Tests for scripts/protected_senders.py — the shared loader and matcher
used by both the HTML render pass and the Gmail-query build pass.
"""
from __future__ import annotations

import json

import pytest

import build_queries as bq
import process_events as pe
from protected_senders import is_protected, load_protected_senders


def test_load_protected_senders_missing_file_returns_empty(tmp_path):
    assert load_protected_senders(str(tmp_path / "nope.txt")) == []


def test_load_protected_senders_strips_comments_and_blanks(tmp_path):
    path = tmp_path / "protected.txt"
    path.write_text(
        "# top-of-file comment\n"
        "\n"
        "fcps.edu  # trailing comment\n"
        "\n"
        "*pta.org\n"
        "# another comment\n"
    )
    assert load_protected_senders(str(path)) == ["fcps.edu", "*pta.org"]


def test_load_protected_senders_lowercases_and_dedupes(tmp_path):
    path = tmp_path / "protected.txt"
    path.write_text("FCPS.edu\nfcps.edu\n*PTA.ORG\n")
    assert load_protected_senders(str(path)) == ["fcps.edu", "*pta.org"]


def test_is_protected_exact_match():
    assert is_protected("fcps.edu", ["fcps.edu"]) is True
    assert is_protected("FCPS.EDU", ["fcps.edu"]) is True
    assert is_protected("other.edu", ["fcps.edu"]) is False


def test_is_protected_bare_domain_matches_subdomains():
    """Bare-domain pattern protects the registrable domain AND every
    subdomain of it (`elementary.fcps.edu`, `a.b.c.pta.org`).

    Preserves the OLD update_auto_blocklist._is_protected semantic
    (now removed, unified into this matcher per #26). The dot
    boundary is required — `notfcps.edu` must not match `fcps.edu`.
    """
    patterns = ["fcps.edu"]
    assert is_protected("fcps.edu", patterns) is True
    assert is_protected("elementary.fcps.edu", patterns) is True
    assert is_protected("a.b.c.fcps.edu", patterns) is True
    # Substring-not-suffix guard: `notfcps.edu` ends with `fcps.edu`
    # in raw chars but is NOT a subdomain of fcps.edu.
    assert is_protected("notfcps.edu", patterns) is False
    # Same guard for an address sender.
    assert is_protected("user@notfcps.edu", patterns) is False
    # And sender at a real subdomain still matches.
    assert is_protected("teacher@school.fcps.edu", patterns) is True


def test_is_protected_suffix_wildcard():
    patterns = ["*pta.org"]
    assert is_protected("louisearcherpta.org", patterns) is True
    assert is_protected("canterburypta.org", patterns) is True
    assert is_protected("pta.org", patterns) is True
    assert is_protected("pta.org.evil.com", patterns) is False


def test_is_protected_empty_domain_is_never_protected():
    assert is_protected("", ["fcps.edu"]) is False
    assert is_protected("   ", ["fcps.edu"]) is False


def test_is_protected_with_empty_pattern_list():
    assert is_protected("fcps.edu", []) is False


# --- Address-form inputs (#20: block keys can now be full addresses) ---

def test_is_protected_address_form_school():
    assert is_protected("alice@fcps.edu", ["fcps.edu"]) is True
    assert is_protected("Alice@FCPS.EDU", ["fcps.edu"]) is True


def test_is_protected_address_form_wildcard():
    assert is_protected("coach@louisearcherpta.org", ["*pta.org"]) is True


def test_is_protected_address_form_unprotected():
    assert is_protected("alice@gmail.com", ["fcps.edu", "*pta.org"]) is False


def test_is_protected_edge_trailing_at():
    # No domain after the '@' — defensive early return so a malformed
    # sheet entry can't slip into the Gmail query.
    assert is_protected("alice@", ["fcps.edu"]) is False


# --- Address-form patterns (#26: protect specific addresses, not just domains) ---
#
# The Ellen failure mode (item 26 design note) was: the agent flagged
# ellen.n.holmes@gmail.com as adult-only from one tax email, the
# auto-blocklist accepted it, and from then on every Gmail query carried
# -from:ellen.n.holmes@gmail.com. We can't protect "gmail.com" wholesale
# (every personal Gmail block lives there), so per-address patterns are
# the load-bearing fix. These tests pin that semantic.


def test_is_protected_address_form_pattern_matches_address():
    """A pattern containing '@' matches the full address (case-insensitive)."""
    patterns = ["ellen.n.holmes@gmail.com"]
    assert is_protected("ellen.n.holmes@gmail.com", patterns) is True
    assert is_protected("Ellen.N.Holmes@GMAIL.COM", patterns) is True


def test_is_protected_address_form_pattern_rejects_other_address_same_domain():
    """Pattern protects ONE address, not the whole domain.

    Load-bearing — the protected list contains
    `ellen.n.holmes@gmail.com` and `thomas.holmes1371@gmail.com`, and
    we must NOT accidentally protect every gmail.com sender (which
    would break the entire freemail-block mechanism from #20).
    """
    patterns = ["ellen.n.holmes@gmail.com"]
    assert is_protected("someone.else@gmail.com", patterns) is False
    assert is_protected("thomas.holmes1371@gmail.com", patterns) is False


def test_is_protected_address_form_pattern_rejects_bare_domain_sender():
    """A bare-domain sender (no `@`) does NOT match an address-form pattern.

    A pattern like `alice@example.com` only protects the specific
    mailbox, not every mailbox at example.com. Bare-domain senders
    must continue to need a bare-domain pattern to be protected.
    """
    patterns = ["ellen.n.holmes@gmail.com"]
    assert is_protected("gmail.com", patterns) is False


def test_is_protected_bare_domain_still_protects_address_sender():
    """Regression pin for #20 behavior: a bare-domain pattern still
    protects every address under that domain.

    The address-form addition must not regress the bare-domain →
    address-sender path that the existing `fcps.edu`, `*pta.org`,
    `teamsnap.com`, etc. patterns rely on.
    """
    patterns = ["fcps.edu"]
    assert is_protected("alice@fcps.edu", patterns) is True
    assert is_protected("teacher@fcps.edu", patterns) is True


def test_is_protected_mixed_pattern_list_honors_each_shape():
    """All three pattern shapes coexist in production — pin that the
    matcher routes each sender to the right rule."""
    patterns = [
        "fcps.edu",                         # bare domain
        "*pta.org",                         # domain-suffix
        "ellen.n.holmes@gmail.com",         # address-form
        "thomas.holmes1371@gmail.com",      # address-form
    ]
    # Bare-domain pattern protects an fcps.edu address.
    assert is_protected("ms.sahai@fcps.edu", patterns) is True
    # Suffix pattern protects a louisearcherpta.org address.
    assert is_protected("president@louisearcherpta.org", patterns) is True
    # Address-form pattern protects each parent's exact address.
    assert is_protected("ellen.n.holmes@gmail.com", patterns) is True
    assert is_protected("thomas.holmes1371@gmail.com", patterns) is True
    # Other gmail.com addresses unprotected.
    assert is_protected("random@gmail.com", patterns) is False
    # Address with no matching pattern unprotected.
    assert is_protected("somebody@example.com", patterns) is False


# --- Build-queries integration: protected domains filtered out of the union ---

def test_build_queries_drops_protected_from_ignored_senders(tmp_path, monkeypatch):
    ignored = tmp_path / "ignored_senders.json"
    ignored.write_text(json.dumps([
        {"domain": "louisearcherpta.org"},  # protected via *pta.org
        {"domain": "teamsnap.com"},         # protected exact
        {"domain": "spammer.com"},          # not protected
    ]))
    protected = tmp_path / "protected.txt"
    protected.write_text("teamsnap.com\n*pta.org\n")

    import io
    import sys

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "argv", [
        "build_queries.py",
        "--blocklist", "",
        "--auto-blocklist", "",
        "--ignored-senders", str(ignored),
        "--protected-senders", str(protected),
        "--today", "2026-04-15",
    ])
    assert bq.main() == 0
    out = json.loads(buf.getvalue())
    excl = out["exclusions"]
    assert excl["protected_senders_size"] == 2
    assert excl["ignored_senders_dropped_protected"] == 2
    assert excl["blocklist_size_ignored_senders"] == 1
    # Only the non-protected domain ends up in the Gmail clause.
    sample = out["queries"]["school_activities"]
    assert "-from:spammer.com" in sample
    assert "-from:teamsnap.com" not in sample
    assert "-from:louisearcherpta.org" not in sample


def test_build_queries_drops_address_form_protected_from_ignored_senders(
    tmp_path, monkeypatch,
):
    # #20 load-bearing guarantee: an address-form block key whose
    # domain part matches a protected pattern must still drop from the
    # Gmail exclusion union. This is the end-to-end check that the
    # address-aware is_protected reaches build_queries's filter.
    ignored = tmp_path / "ignored_senders.json"
    ignored.write_text(json.dumps([
        {"domain": "alice@fcps.edu"},       # protected via fcps.edu
        {"domain": "coach@louisearcherpta.org"},  # protected via *pta.org
        {"domain": "alice@gmail.com"},      # not protected (freemail)
        {"domain": "jane@outlook.com"},     # not protected (freemail)
    ]))
    protected = tmp_path / "protected.txt"
    protected.write_text("fcps.edu\n*pta.org\n")

    import io
    import sys

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "argv", [
        "build_queries.py",
        "--blocklist", "",
        "--auto-blocklist", "",
        "--ignored-senders", str(ignored),
        "--protected-senders", str(protected),
        "--today", "2026-04-15",
    ])
    assert bq.main() == 0
    out = json.loads(buf.getvalue())
    excl = out["exclusions"]
    assert excl["ignored_senders_dropped_protected"] == 2
    assert excl["blocklist_size_ignored_senders"] == 2
    sample = out["queries"]["school_activities"]
    # Address-form freemail rows land in the Gmail clause unchanged —
    # Gmail's from: operator accepts either shape.
    assert "-from:alice@gmail.com" in sample
    assert "-from:jane@outlook.com" in sample
    # Address-form protected rows drop.
    assert "-from:alice@fcps.edu" not in sample
    assert "-from:coach@louisearcherpta.org" not in sample


# --- Render integration: Ignore-sender button suppressed for protected senders ---

def test_render_html_omits_ignore_sender_button_for_protected_sender():
    import datetime as dt

    event = {
        "id": "evt1",
        "name": "Field trip",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "9:00 AM",
        "location": "School",
        "category": "School",
        "child": "Kid",
        "source": "test@fcps.edu",
        "sender_domain": "fcps.edu",
        "sender_block_key": "fcps.edu",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=dt.date(2026, 4, 15),
        weeks=weeks,
        undated=[],
        total_future=1,
        lookback_days=60,
        webhook_url="https://example.com/hook",
        pages_url="",
        protected_senders=["fcps.edu"],
    )
    assert 'data-sender="fcps.edu"' in html  # the card attribute remains
    # The CSS rule for .ignore-sender-btn still exists (harmless), but no
    # actual <button class="ignore-sender-btn"> element is rendered.
    assert 'button class="ignore-sender-btn"' not in html


def test_render_html_keeps_ignore_sender_button_for_unprotected_sender():
    import datetime as dt

    event = {
        "id": "evt2",
        "name": "Field trip",
        "date": "2026-04-20",
        "_date_obj": dt.date(2026, 4, 20),
        "time": "9:00 AM",
        "location": "School",
        "category": "School",
        "child": "Kid",
        "source": "test@spammer.com",
        "sender_domain": "spammer.com",
        "sender_block_key": "spammer.com",
    }
    weeks = [(dt.date(2026, 4, 20), [event])]
    html = pe.render_html(
        today=dt.date(2026, 4, 15),
        weeks=weeks,
        undated=[],
        total_future=1,
        lookback_days=60,
        webhook_url="https://example.com/hook",
        pages_url="",
        protected_senders=["fcps.edu"],
    )
    assert 'button class="ignore-sender-btn"' in html
    assert 'data-sender="spammer.com"' in html


def test_render_html_omits_ignore_sender_button_for_protected_address_form_sender():
    """ROADMAP #28 regression pin. Pre-fix, the render-time guard called
    `is_protected(domain, protected)` — passing the bare registrable
    domain (e.g. `gmail.com`) for a freemail sender. Address-form
    patterns added in #26 only match when the sender is itself an
    address (`alice@gmail.com`), so the bare-domain query never fired
    and the button rendered for protected freemail addresses like
    Ellen's. The fix passes `block_key` (the full address for freemail
    senders) so address-form patterns match correctly here too —
    closing the fat-finger gap where someone could click "Ignore
    sender" on Ellen's self-note card."""
    import datetime as dt

    event = {
        "id": "evt3",
        "name": "Volleyball tryouts",
        "date": "2026-05-04",
        "_date_obj": dt.date(2026, 5, 4),
        "time": "8:00 AM",
        "location": "School gym",
        "category": "Sports",
        "child": "Everly",
        "source": "ellen.n.holmes@gmail.com",
        "sender_domain": "gmail.com",
        "sender_block_key": "ellen.n.holmes@gmail.com",
    }
    weeks = [(dt.date(2026, 5, 4), [event])]
    html = pe.render_html(
        today=dt.date(2026, 4, 25),
        weeks=weeks,
        undated=[],
        total_future=1,
        lookback_days=60,
        webhook_url="https://example.com/hook",
        pages_url="",
        protected_senders=["ellen.n.holmes@gmail.com"],
    )
    # data-sender carries the full address for freemail block keys.
    assert 'data-sender="ellen.n.holmes@gmail.com"' in html
    # No actual <button class="ignore-sender-btn"> element rendered —
    # the fat-finger path is closed at render time.
    assert 'button class="ignore-sender-btn"' not in html
    # The Ignore-event button on the SAME card is unaffected — the
    # user can still hide a single event from a protected sender.
    # Pin this so a future "tighten protection" pass doesn't
    # accidentally over-suppress the per-event ignore too.
    assert 'button class="ignore-btn"' in html


def test_render_html_keeps_ignore_sender_button_for_unprotected_freemail_address():
    """Parity to the institutional-unprotected case: an unprotected
    freemail address (the typical `spammer.123@gmail.com` shape from
    item #20 freemail-block-granularity) must STILL get an Ignore-
    sender button. Otherwise the fix to `block_key` would over-suppress
    and break per-address blocking for freemail."""
    import datetime as dt

    event = {
        "id": "evt4",
        "name": "Junk newsletter",
        "date": "2026-04-30",
        "_date_obj": dt.date(2026, 4, 30),
        "time": "9:00 AM",
        "location": "Inbox",
        "category": "Other",
        "child": "Kid",
        "source": "spammer.123@gmail.com",
        "sender_domain": "gmail.com",
        "sender_block_key": "spammer.123@gmail.com",
    }
    weeks = [(dt.date(2026, 4, 30), [event])]
    html = pe.render_html(
        today=dt.date(2026, 4, 25),
        weeks=weeks,
        undated=[],
        total_future=1,
        lookback_days=60,
        webhook_url="https://example.com/hook",
        pages_url="",
        # Ellen's address is protected; spammer.123 is NOT, even though
        # it shares the gmail.com domain. Pin: address-form protection
        # is per-address, not per-domain.
        protected_senders=["ellen.n.holmes@gmail.com"],
    )
    assert 'button class="ignore-sender-btn"' in html
    assert 'data-sender="spammer.123@gmail.com"' in html
