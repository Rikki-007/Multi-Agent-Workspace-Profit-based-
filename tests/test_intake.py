"""Omni-channel intake: normalisation, the public web endpoint, and routing into the pipeline."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agency import graph as G
from agency.intake import BRIEF_CODE, IntakeError, IntakeSource, normalize
from agency.jobqueue import JobQueue
from agency.lessons import LessonStore
from agency.schemas import JobState, SiteStyle, TemplateTier
from server import create_app

NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)
SITE = "https://www.monolythzro.com"


def norm(payload):
    return normalize(payload, source=IntakeSource.WEB, intake_id="int_test", received_at=NOW)


# ---------------------------------------------------------------- normalisation


def test_code_alone_sets_tier_and_style():
    i = norm({"brief_code": "mz-t2-glass-7f3k", "contact": {"email": "ana@example.com"}})
    assert (i.brief_code, i.tier, i.style) == ("MZ-T2-GLASS-7F3K", TemplateTier.T2, SiteStyle.GLASSMORPHISM)
    assert i.status.value == "pending_review"


def test_flat_camelcase_fields_and_labels_are_understood():
    i = norm({"briefCode": None, "tier": "3", "style": "Liquid Glass", "fullName": "Ana Byrne", "phoneNumber": "+353 89 613 9970"})
    assert (i.tier, i.style) == (TemplateTier.T3, SiteStyle.LIQUID_GLASS)
    assert BRIEF_CODE.fullmatch(i.brief_code) and i.brief_code.startswith("MZ-T3-LIQUID-")  # generated server-side
    assert (i.contact.name, i.contact.phone) == ("Ana Byrne", "+353896139970")


def test_code_pasted_inside_a_chat_message():
    msg = "Project brief: MZ-T1-BRUTAL-AB2C\nTier: T1 Editorial\nHi, I'd like to start a project."
    i = norm({"message": msg, "whatsapp": "353896139970"})
    assert (i.brief_code, i.tier, i.style) == ("MZ-T1-BRUTAL-AB2C", TemplateTier.T1, SiteStyle.NEO_BRUTALISM)


@pytest.mark.parametrize(
    "payload, error",
    [
        ({"brief_code": "MZ-T2-GLASS-7F3K", "tier": "T3", "email": "a@example.com"}, "doesn't match"),
        ({"brief_code": "MZ-T2-GLASS-7F3K"}, "email address or a phone number"),
        ({"tier": "T2", "email": "a@example.com"}, "brief code, or both a tier and a style"),
        ({"tier": "T9", "style": "glass", "email": "a@example.com"}, "unknown tier"),
        ({"tier": "T2", "style": "vaporwave", "email": "a@example.com"}, "unknown style"),
        ({"brief_code": "MZ-T2-GLASS-7F3K", "email": "not-an-email"}, "invalid email"),
        ({"brief_code": "MZ-T2-GLASS-7F3K", "phone": "call me maybe"}, "invalid phone"),
        (["not", "an", "object"], "JSON object"),
    ],
)
def test_bad_submissions_explain_themselves(payload, error):
    with pytest.raises(IntakeError, match=error):
        norm(payload)


def test_ambiguous_characters_never_appear_in_codes():
    assert not BRIEF_CODE.search("MZ-T2-GLASS-0O1I")


# ---------------------------------------------------------------- the web endpoint


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "q.sqlite", None, tmp_path / "lessons.sqlite")
    return TestClient(app), tmp_path


def post(c, body, origin=SITE):
    return c.post("/api/intake/web", json=body, headers={"Origin": origin})


def test_web_intake_is_stored_deduped_and_cors_scoped(client):
    c, _ = client
    body = {"brief_code": "MZ-T2-GLASS-7F3K", "tier": "T2", "style": "glassmorphism", "contact": {"email": "ana@example.com"}}
    first = post(c, body)
    assert first.status_code == 201 and first.json()["status"] == "pending_review"
    assert first.headers["access-control-allow-origin"] == SITE

    again = post(c, body)  # double click / retry
    assert again.status_code == 200 and again.json()["intake_id"] == first.json()["intake_id"]
    assert len(c.get("/api/intakes").json()) == 1

    other = post(c, body, origin="https://evil.example")
    assert "access-control-allow-origin" not in other.headers  # the browser will block the response
    assert c.options("/api/intake/web", headers={"Origin": SITE}).status_code == 204
    assert c.options("/api/intake/web", headers={"Origin": "https://evil.example"}).status_code == 403


def test_web_intake_guards(client):
    c, _ = client
    assert post(c, {"brief_code": "MZ-T2-GLASS-7F3K"}).json()["detail"].startswith("add an email")
    assert c.post("/api/intake/web", content=b"{" + b" " * 20_000 + b"}", headers={"Content-Type": "application/json"}).status_code == 413
    bot = post(c, {"brief_code": "MZ-T1-MINIMAL-AAAA", "email": "bot@example.com", "website": "http://spam"})
    assert bot.status_code == 201 and c.get("/api/intakes").json() == []  # looks accepted, never stored
    codes = [post(c, {"tier": "T1", "style": "minimalism", "email": f"p{i}@example.com"}).status_code for i in range(6)]
    assert 429 in codes  # rate limit kicks in within the minute


# ---------------------------------------------------------------- review and routing


def test_routed_intake_becomes_a_job_built_at_the_briefed_tier(client, tmp_path):
    c, path = client
    intake = post(c, {"brief_code": "MZ-T3-LIQUID-9QX4", "contact": {"name": "Ana Byrne", "company": "Byrne Audio", "email": "ana@example.com"}}).json()

    routed = c.post(f"/api/intakes/{intake['intake_id']}/route", json={"notes": "good fit"})
    assert routed.status_code == 201
    assert routed.json()["status"] == "routed" and routed.json()["job_id"] == f"job-{intake['intake_id']}"
    assert c.post(f"/api/intakes/{intake['intake_id']}/route").status_code == 409  # never twice
    assert c.get("/api/intakes", params={"status": "pending_review"}).json() == []

    # The queued START payload carries lead + brief; run it through the graph.
    [item] = JobQueue(path / "q.sqlite").list()
    state = JobState.model_validate({**item.payload, "job_id": item.job_id})
    assert state.lead.business_name == "Byrne Audio" and state.lead.source == f"intake:{intake['intake_id']}"
    graph = G.build_graph().compile(checkpointer=InMemorySaver(serde=G._serde()))
    ctx = G.AgencyContext(lessons=LessonStore(tmp_path / "l2.sqlite"))
    config = G.job_config(item.job_id)
    graph.invoke(state, config, context=ctx)
    graph.invoke(Command(resume={"approved": True}), config, context=ctx)
    spec = JobState.model_validate(graph.get_state(config).values).spec
    assert (spec.tier, spec.style) == (TemplateTier.T3, SiteStyle.LIQUID_GLASS)


def test_rejected_intake_cannot_be_routed(client):
    c, _ = client
    intake = post(c, {"tier": "T1", "style": "minimalism", "phone": "+353896139970"}).json()
    assert c.post(f"/api/intakes/{intake['intake_id']}/reject", json={"notes": "out of scope"}).json()["status"] == "rejected"
    assert c.post(f"/api/intakes/{intake['intake_id']}/route").status_code == 409
    assert c.post("/api/intakes/int_nope/reject").status_code == 404
