#!/usr/bin/env python3
"""Every threshold, path, mode and backend order the judge uses — in one file.

Nothing else in this package hardcodes a number or a directory. If a band moves,
it moves here, and the decision log records the version that was in force.

THE MODE GATE — TWO SWITCHES
--------------------------------------------
`CALIBRATED_JUDGE_MODE` says how far the judge may go AT ALL:

    off      (default)  no call, no spend, no opinion. A process that has never
                        heard of this module behaves exactly as it did before.
    shadow              the judge answers AFTER the incumbent has decided; both
                        are logged; the incumbent's verdict is returned
                        untouched.
    active              the judge's action may replace the incumbent's verdict.

`CALIBRATED_JUDGE_ACTIVE_DECISIONS` — a comma list — says WHICH decisions may
go that far. `effective_mode(decision)` is the gate every seam reads:

    active AND the decision is listed  -> active
    active but the decision is not     -> shadow
    shadow                             -> shadow (the list cannot widen it)
    off / a typo                       -> off

Precedence for both: a process variable beats the repo `.env`; within one
`.env` the LAST matching line wins (it is append-only). The one difference is
deliberate — an EMPTY `CALIBRATED_JUDGE_ACTIVE_DECISIONS` in the process
environment means "nothing acts here" and does NOT fall back to the file.

Anything unrecognised reads as `off`. A typo must never turn the judge on, and
turning the global switch on must never activate a decision nobody measured.
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------- modes
MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ACTIVE = "active"
MODES = (MODE_OFF, MODE_SHADOW, MODE_ACTIVE)
ENV_MODE = "CALIBRATED_JUDGE_MODE"



ENV_ACTIVE_DECISIONS = "CALIBRATED_JUDGE_ACTIVE_DECISIONS"


def _env_file_value(key, start=None):
    """The value of `key` in the nearest `.env` above `start`, or "".

    Two rules, both of them consequences of treating `.env` as APPEND-ONLY:
    within one file the LAST matching line wins (a line appended later is the
    newer decision — the rule dotenv itself follows), and a nearer `.env` that
    never mentions the key is not an answer, so the walk continues up to the
    repo's own. Only that one value is read out; nothing else in the file is
    touched, and an unreadable file is "" rather than an exception.
    """
    from pathlib import Path as _P
    p = _P(start or __file__).resolve()
    for d in [p.parent, *p.parents]:
        env = d / ".env"
        if env.is_file():
            found = None
            try:
                for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.startswith(key + "="):
                        found = line.split("=", 1)[1].strip().strip('"').strip("'").lower()
            except OSError:
                return ""
            if found is not None:
                return found
            # a nearer .env without the line (e.g. a tool's own key file) is
            # not an answer — keep walking up to the repo's .env
            continue
    return ""


def _mode_from_env_file(start=None):
    """Fallback when ENV_MODE is not set in the process environment: read it from
    the nearest `.env` above this file (the per-machine config you own).
    An explicit process variable always wins; no `.env`, or no line, means off.
    Returns the bare mode word only — nothing else from the file is read out."""
    return _env_file_value(ENV_MODE, start)


def _active_decisions_from_env_file(start=None):
    """The same file and the same walk, for the per-decision activation list."""
    return _env_file_value(ENV_ACTIVE_DECISIONS, start)


def active_decisions(explicit=None):
    """The decisions allowed to ACT while the global mode is `active`.

    PRECEDENCE, and why it differs from the mode's by one deliberate step: a
    process variable that is PRESENT wins even when it is EMPTY. `MODE` falls
    back to the `.env` on an empty string; this list must not, because
    `CALIBRATED_JUDGE_ACTIVE_DECISIONS= ` is how an operator says "let nothing
    act in this process", and silently restoring the machine's list would turn
    a decision the operator just switched off back on. Unset everywhere is the
    empty tuple: nothing acts until something names it.
    """
    if explicit is None:
        raw = os.environ.get(ENV_ACTIVE_DECISIONS)
        if raw is None:
            raw = _active_decisions_from_env_file()
    else:
        raw = explicit
    parts = ([str(x) for x in raw]
             if isinstance(raw, (list, tuple, set, frozenset))
             else str(raw or "").split(","))
    out = []
    for part in parts:
        name = " ".join(str(part).strip().lower().split())
        if name and name not in out:
            out.append(name)
    return tuple(out)


def effective_mode(decision, explicit=None, decisions=None):
    """The mode in force FOR ONE DECISION — the only gate a seam should read.

        off                         -> off        (nothing runs, nothing spends)
        shadow                      -> shadow     (whatever the list says)
        active + decision listed    -> active
        active + decision NOT listed-> shadow

    Two switches, and the narrow one cannot widen the broad one: a decision
    named in the list while the global mode is `shadow` still only observes.
    """
    current = mode(explicit)
    if current != MODE_ACTIVE:
        return current
    name = " ".join(str(decision or "").strip().lower().split())
    return MODE_ACTIVE if name in active_decisions(decisions) else MODE_SHADOW

def mode(explicit: str | None = None) -> str:
    """The mode in force. An explicit argument beats the environment;
    anything unrecognised — including a typo — degrades to `off`."""
    raw = explicit if explicit is not None else (os.environ.get(ENV_MODE, "") or _mode_from_env_file())
    value = str(raw or "").strip().lower()
    return value if value in MODES else MODE_OFF


# ------------------------------------------------------------------ backends
#: Direct TypeSafe first (it is the model's home and the cheapest route when a
#: key exists), OpenRouter second — the route verified live. The
#: client falls through on its own when a backend has no credential.
BACKEND_ORDER = ("typesafe", "openrouter")
ENV_BACKENDS = "CALIBRATED_JUDGE_BACKENDS"


def backend_order() -> tuple[str, ...]:
    raw = os.environ.get(ENV_BACKENDS, "")
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts or BACKEND_ORDER


# ---------------------------------------------------------------- thresholds
#: Defaults for the fallback composer. When the bake-off ships `THRESHOLDS_V2`
#: those values win — one number, one owner.
THRESHOLDS = {
    # a support question must clear this before it can carry an accept
    "accept_support": 0.90,
    # a named disqualifier must clear this before it can carry a rejection
    "reject_claim": 0.90,
    # a review reason fires at this probability and beats accept AND reject
    "review_trigger": 0.90,
}


#: The bake-off's v2 band names, mapped onto ours. One number, one owner: when
#: `THRESHOLDS_V2` exists it wins, so the production judge and the bake-off that
#: measures it can never drift apart.
V2_ALIASES = {"accept_support": ("accept", "accept_support"),
              "reject_claim": ("reject_claim",),
              "review_trigger": ("review_at", "review_trigger")}


def thresholds() -> dict:
    """The house numbers, overlaid with the bake-off's v2 bands when present."""
    out = dict(THRESHOLDS)
    try:
        from bakeoff import batteries as B  # noqa: PLC0415
    except Exception:
        return out
    v2 = getattr(B, "THRESHOLDS_V2", None)
    if isinstance(v2, dict):
        for ours, theirs in V2_ALIASES.items():
            for name in theirs:
                if name in v2:
                    try:
                        out[ours] = float(v2[name])
                    except (TypeError, ValueError):
                        pass
                    break
    return out


# ----------------------------------------------------------------- decisions
#: The five identity decisions live in the bake-off's batteries; the two fit
#: decisions live in `judge/fit.py` because nothing upstream owns them yet.
IDENTITY_DECISIONS = ("real_website", "employment_now", "same_identity",
                      "site_status", "role_holder_now")
FIT_DECISIONS = ("icp_fit", "persona_fit")
DECISIONS = IDENTITY_DECISIONS + FIT_DECISIONS

#: The one question whose probability is the headline for calibration scoring.
#: Absent an entry, the battery's first support question is used.
HEADLINE_QUESTION = {
    ("real_website", "v1"): "is_the_orgs_own_site",
    ("real_website", "v2"): "same_organization",
    ("employment_now", "v1"): "current_experience_names_company",
    ("employment_now", "v2"): "employer_is_same_org",
    ("same_identity", "v1"): "names_agree",
    ("site_status", "v1"): "content_is_the_orgs",
    ("site_status", "v2"): "official_publisher",
    ("role_holder_now", "v1"): "page_names_a_holder",
    ("icp_fit", "fit"): "self_description_matches_icp",
    ("persona_fit", "fit"): "title_matches_persona",
}

#: Guards that genuinely license a REJECTION. Every other guard is a review
#: reason. The distinction is the whole doctrine: rejecting a CANDIDATE is not
#: the same claim as asserting the thing does not exist.
REJECT_CLAIMS = {
    "real_website": frozenset((
        "parked_or_for_sale", "social_only", "directory_listing",
        "redirects_off_domain", "different_org_same_name",
        "page_is_unrelated_business")),
    "employment_now": frozenset(("only_a_past_role", "role_explicitly_ended")),
    "same_identity": frozenset(("identifier_conflicts", "timeline_impossible")),
    "site_status": frozenset(("error_page_body", "error_page")),
    # UNKNOWN is an absence claim; no guard here may assert it
    "role_holder_now": frozenset(),
    "icp_fit": frozenset(("excluded_class_stated",)),
    "persona_fit": frozenset(("self_declared_not_working",)),
}

#: Reject verdicts that are really ABSENCE claims. The evidence in front of a
#: no-tools judge can disqualify a candidate; it can never prove that no site,
#: and no holder, exists anywhere. Those rejections land on
#: EVIDENCE_INSUFFICIENT instead.
ABSENCE_VERDICTS = {
    "real_website": "NO_WEBSITE",
    "role_holder_now": "UNKNOWN",
}
EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"

#: What an honest "we could not decide" is called, per decision.
UNSURE_VERDICT = {
    "real_website": EVIDENCE_INSUFFICIENT,
    "employment_now": "UNSURE",
    "same_identity": "UNSURE",
    "site_status": "unknown",
    "role_holder_now": "UNKNOWN",
    "icp_fit": "UNSURE",
    "persona_fit": "UNSURE",
}


def headline_question(decision: str, version: str, battery: dict | None = None):
    """The question a reliability curve is drawn on."""
    qid = HEADLINE_QUESTION.get((decision, version))
    if qid:
        return qid
    for q in (battery or {}).get("questions", []):
        if q.get("polarity", "good") == "good":
            return q["id"]
    return None


# --------------------------------------------------------------------- paths
ENV_ROOT = "GTM_STARTER_ROOT"
ENV_OUT = "CALIBRATED_JUDGE_OUT"


def blueprint_root() -> Path:
    """The starter repo root when this skill is inside one, else the skill folder.

    Walk up looking for START-HERE.md (the repo's marker). A standalone install
    (`~/.claude/skills/calibrated-judge`) has no repo above it, so everything the
    judge writes stays inside the installed skill.
    """
    env = os.environ.get(ENV_ROOT)
    if env:
        return Path(env).expanduser()
    skill = skill_dir()
    for p in (skill, *skill.parents):
        if (p / "START-HERE.md").is_file():
            return p
    return skill


def skill_dir() -> Path:
    """…/skills/calibrated-judge"""
    return Path(__file__).resolve().parents[2]


def out_dir() -> Path:
    """Everything this module writes. `outputs/` is gitignored at the repo
    root, so no decision log, no actual and no report can reach a commit."""
    env = os.environ.get(ENV_OUT)
    if env:
        return Path(env).expanduser()
    return blueprint_root() / "outputs" / "calibrated-judge"


def decisions_dir() -> Path:
    return out_dir() / "decisions"


def actuals_path() -> Path:
    return out_dir() / "actuals.jsonl"


def reports_dir() -> Path:
    return out_dir() / "reports"


#: Eval files a ratification Sheet writes back into (not shipped here).
GROUND_TRUTH = {
    "linkedin-validator": "evals/linkedin-validator/ground_truth.jsonl",
    "contact-finder": "evals/contact-finder/ground_truth.jsonl",
    "tam-contact-harvester": "evals/tam-contact-harvester/ground_truth.jsonl",
}

#: Aliases a reviewer may type in the Sheet's `set` column.
SET_ALIASES = {
    "lv": "linkedin-validator", "li": "linkedin-validator",
    "profile-check": "linkedin-validator", "linkedin": "linkedin-validator",
    "linkedin_validator": "linkedin-validator",
    "cf": "contact-finder", "contact_finder": "contact-finder",
    "tch": "tam-contact-harvester", "harvester": "tam-contact-harvester",
    "tam": "tam-contact-harvester",
    "tam_contact_harvester": "tam-contact-harvester",
}

PENDING_MARKER = "PENDING-REVIEW"
RATIFIED_PREFIX = "ratified-"
EXCLUDED_PREFIX = "excluded-"
EXCLUDE_TOKENS = ("exclude", "excluded", "drop", "remove")


def ground_truth_paths() -> dict:
    root = blueprint_root()
    return {name: root / rel for name, rel in GROUND_TRUTH.items()}


def canonical_set(name: str) -> str | None:
    key = str(name or "").strip().lower().replace(" ", "-")
    if key in GROUND_TRUTH:
        return key
    return SET_ALIASES.get(key.replace("-", "_"), SET_ALIASES.get(key))
