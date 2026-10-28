"""Narrow, advisory decisions through TypeSafe's official System One API."""

import asyncio
import math

import httpx

from .config import settings

API_URL = "https://api.typesafe.ai/v1/systemone"
_limit = asyncio.Semaphore(4)


class JevUnavailable(Exception):
    pass


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}


def _validated(questions: dict, answers: dict) -> dict:
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise JevUnavailable("Jev returned an incomplete decision")
    result = {}
    for name, question in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise JevUnavailable("Jev returned an invalid decision")
        confidence = answer.get("confidence")
        if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise JevUnavailable("Jev returned an invalid confidence")
        if question["type"] == "choice":
            value = answer.get("choice")
            if value not in question["criteria"]:
                raise JevUnavailable("Jev returned an unknown choice")
        else:
            value = answer.get("score")
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= len(question["criteria"]) - 1:
                raise JevUnavailable("Jev returned an invalid score")
            value = round(float(value), 2)
        result[name] = {"value": value, "confidence": round(float(confidence), 3)}
    return result


async def decide(state: dict, questions: dict) -> dict:
    body = {"model": settings.typesafe_model, "state": state, "questions": questions}
    try:
        async with _limit, httpx.AsyncClient(timeout=12) as client:
            response = await client.post(
                API_URL,
                json=body,
                headers={"Authorization": f"Bearer {settings.typesafe_api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise JevUnavailable("Jev is unavailable; retry the decision later") from exc
    if not isinstance(payload, dict):
        raise JevUnavailable("Jev returned an invalid response")
    return {"model": payload.get("model", settings.typesafe_model), "decisions": _validated(questions, payload.get("answers"))}


FINDING_QUESTIONS = {
    "priority": score("How urgently should a human investigate this finding, given impact and evidence?", [
        "Informational or low impact", "Worth reviewing when convenient", "Review soon", "Review promptly because likely impact is high",
    ]),
    "triage": choice("Choose the investigation queue for this finding.", {
        "review_now": "Evidence and likely impact warrant prompt human review",
        "review_later": "Worth investigating, but no prompt action is indicated",
        "informational": "Useful context with little action needed",
    }),
    "relevance": choice("How relevant is this finding likely to be to the database design? Do not treat names alone as proof.", {
        "likely_relevant": "Evidence supports a real design or operational concern",
        "possibly_relevant": "The concern depends on workload or business meaning not supplied",
        "probably_intentional": "Available evidence suggests a deliberate design choice",
    }),
    "missing_evidence": choice("What evidence is most needed before acting on this recommendation?", {
        "none": "Enough evidence is present for a human to review the recommendation",
        "workload": "Representative queries, frequency, or performance measurements are needed",
        "business_context": "Business meaning, intended data semantics, or ownership must be confirmed",
        "schema_detail": "More complete schema metadata or a fresh scan is needed",
    }),
}
DIFF_QUESTIONS = {
    "priority": score("How urgently should a human review this observed schema change?", [
        "Routine change", "Review when convenient", "Review soon", "Prompt review for possible breakage",
    ]),
    "triage": choice("Choose the review queue for this schema change.", {
        "review_now": "Potentially breaking or high impact change",
        "review_later": "Change merits review but appears less urgent",
        "informational": "Routine or additive change",
    }),
}

PROPOSAL_QUESTIONS = {
    "readiness": choice("Is this design proposal ready for human review based on its evidence and stated limitations?", {
        "ready_for_review": "Concrete enough for a human to assess, with evidence and limits visible",
        "needs_context": "Needs workload, business meaning, or implementation context",
        "too_speculative": "The proposed change is too vague or unsupported to review",
    }),
}

RUN_QUESTIONS = {
    "attention": score("How much human attention does this run need now?", [
        "Routine completed activity", "Worth checking later", "Needs review soon", "Needs prompt attention",
    ]),
    "triage": choice("Choose the attention queue for this run.", {
        "needs_attention": "Failure, interruption, or other consequential result needs human attention",
        "review_later": "May be useful to inspect later",
        "routine": "No particular attention needed",
    }),
}
