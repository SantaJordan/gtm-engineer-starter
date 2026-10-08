#!/usr/bin/env python3
"""Deterministic checks — everything v1 asked a Noul to compute.

An adversarial review found the same shape eleven times: a
question that looks semantic but is actually arithmetic, date ordering, string
normalization or a hostname comparison. TypeSafe's own limitations page names
those as the model's weak spots, and a page of untrusted text can steer any of
them. So they live here, in code, where they are testable and unsteerable.

Every function returns a THREE-valued answer where a missing prerequisite is
possible: True / False / None, and None means "unavailable", never False.
"""
from __future__ import annotations

import re
from datetime import date

from .decisions import registrable_domain, norm_name, norm_text

# --------------------------------------------------------------------- hosts
#: maintained social-host list, matched on hostname BOUNDARIES (a label suffix),
#: so `notfacebook.com` and `facebook.com.evil.org` are not social.
SOCIAL_HOSTS = frozenset({
    "facebook.com", "fb.com", "fb.me", "instagram.com", "twitter.com", "x.com",
    "linkedin.com", "tiktok.com", "youtube.com", "youtu.be", "nextdoor.com",
    "threads.net", "pinterest.com", "snapchat.com", "vk.com", "reddit.com",
})

#: generic words that carry no distinguishing power in an organization name
_GENERIC_NAME_TOKENS = {
    "the", "and", "of", "inc", "incorporated", "llc", "ltd", "co", "corp",
    "corporation", "company", "department", "dept", "fire", "rescue", "ems",
    "district", "volunteer", "association", "assn", "service", "services",
    "town", "city", "county", "village", "township", "borough", "station",
    "protection", "emergency", "authority", "commission", "board", "no",
}

_LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "l.l.c", "ltd", "limited",
                   "co", "corp", "corporation", "plc", "lp", "llp", "pc"}


# ----------------------------------------------------------------- transport
def transport_class(candidate) -> str:
    """The observed transport fact: 2xx|3xx|4xx|5xx|timeout|no_dns|unobserved.

    A failed fetch is never a status, and a status is never a failed fetch.
    `no_dns` and `timeout` stay different facts because the review's central
    complaint about site_status was that v1 collapsed them into `dead`.
    """
    c = candidate if isinstance(candidate, dict) else {}
    kind = c.get("fetch_failure_kind")
    status = c.get("http_status")
    if kind == "no_dns":
        return "no_dns"
    if kind == "transient" and status is None:
        return "timeout"
    if status is None:
        return "timeout" if kind == "transient" else "unobserved"
    try:
        n = int(status)
    except (TypeError, ValueError):
        return "unobserved"
    if 200 <= n < 300:
        return "2xx"
    if 300 <= n < 400:
        return "3xx"
    if 400 <= n < 500:
        return "4xx"
    if n >= 500:
        return "5xx"
    return "unobserved"


def hostname_of(url) -> str:
    s = str(url or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return s.split("@")[-1].split(":", 1)[0].strip(".")


def is_social_url(url):
    """True / False / None(unresolved). Hostname boundaries, not `in`."""
    host = hostname_of(url)
    if not host:
        return None
    labels = host.split(".")
    for i in range(len(labels) - 1):
        if ".".join(labels[i:]) in SOCIAL_HOSTS:
            return True
    return False


# -------------------------------------------------------------------- phones
_PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")


def _digits(text) -> str:
    d = re.sub(r"\D", "", str(text or ""))
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def phones_in(text):
    return {_digits(m) for m in _PHONE_RE.findall(str(text or ""))
            if len(_digits(m)) == 10}


def phone_match(anchor_digits, page_text):
    """True / False / None. None when either side is missing or redacted."""
    want = _digits(anchor_digits)
    if len(want) != 10:
        return None
    found = phones_in(page_text)
    if not found:
        return None
    return want in found


# --------------------------------------------------------------- name tokens
def name_tokens(name, *, city="", state="", drop=None):
    """The DISTINCTIVE tokens of an organization name, prepared explicitly.

    Returns [] when nothing distinctive survives — the caller must treat that
    as unavailable rather than as a failed match.
    """
    drop = set(drop or ()) | set(_GENERIC_NAME_TOKENS)
    for extra in (city, state):
        for tok in re.split(r"\W+", str(extra or "").lower()):
            if tok:
                drop.add(tok)
    toks = [t for t in re.split(r"\W+", str(name or "").lower())
            if t and t not in drop and not t.isdigit()]
    out = []
    for t in toks:
        if t not in out:
            out.append(t)
    return out


def token_match(tokens, text):
    """Every prepared token present? None when either side is empty."""
    toks = [t for t in (tokens or []) if t]
    body = str(text or "").lower()
    if not toks or not body.strip():
        return None
    return all(t in body for t in toks)


def domain_token_match(domain, tokens):
    """Does the registrable domain spell any prepared token or its initialism?"""
    reg = registrable_domain(domain) or str(domain or "").strip().lower()
    toks = [t for t in (tokens or []) if t]
    if not reg or not toks:
        return None
    label = reg.split(".")[0]
    if any(t in label for t in toks):
        return True
    initials = "".join(t[0] for t in toks)
    return bool(initials) and initials in label


# ----------------------------------------------------------------- redirects
def registrable_change(url, terminus):
    """Did the registrable domain change? None when the terminus is unresolved."""
    a, b = registrable_domain(url), registrable_domain(terminus)
    if not a or not b:
        return None
    return a != b


# ------------------------------------------------------------------ temporal
_ONGOING = ("present", "current", "ongoing", "to date", "now")
_YEAR = re.compile(r"(19|20)\d{2}")


def temporal_status(dates_text, as_of=None):
    """current | ended | future | unknown, computed from the date string.

    `since` with no end date is UNKNOWN, not current: a blank end field is
    missing data. Only explicit present/ongoing wording, or a range whose end
    is in the future, establishes currentness.
    """
    text = str(dates_text or "").strip().lower()
    if not text:
        return "unknown"
    today = date.fromisoformat(as_of) if isinstance(as_of, str) and as_of else \
        (as_of or date.today())
    years = [int(m.group(0)) for m in _YEAR.finditer(text)]
    if any(w in text for w in _ONGOING):
        return "current"
    if len(years) >= 2:
        start, end = years[0], years[-1]
        if start > today.year:
            return "future"
        return "current" if end >= today.year else "ended"
    if len(years) == 1:
        if years[0] > today.year:
            return "future"
        return "unknown"      # a lone start year establishes nothing about now
    return "unknown"


# ------------------------------------------------------- organization names
def norm_org(name) -> str:
    t = re.sub(r"[.,'\"`&]", " ", str(name or "").lower())
    toks = [x for x in t.split() if x and x not in _LEGAL_SUFFIXES]
    return " ".join(toks)


def org_names_equal(a, b):
    """Same employer entity, allowing legal suffixes and organizational units.

    'Stanford University' == 'Stanford'; 'Acme Corp, Inc.' == 'Acme Corp'.
    A shared generic word is never enough — 'Acme Corp' != 'Beta Corp'.
    """
    na, nb = norm_org(a), norm_org(b)
    if not na or not nb:
        return None
    if na == nb:
        return True
    ta = [t for t in na.split() if t not in _GENERIC_NAME_TOKENS]
    tb = [t for t in nb.split() if t not in _GENERIC_NAME_TOKENS]
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return all(t in long_ for t in short)


# ---------------------------------------------------------- experience pick
def select_experience(anchor_org, experiences, as_of=None):
    """Pick ONE experience entry, in code, and say why.

    Employer, time and role type must be judged on the SAME entry or three
    positive answers can describe three different jobs. The pick is an
    employer-name match; when nothing matches, nothing is selected.
    """
    exps = [e for e in (experiences or []) if isinstance(e, dict)]
    for i, e in enumerate(exps):
        if org_names_equal(anchor_org, e.get("company")) is True:
            return {"index": i, "entry": e, "reason": "employer_name_match",
                    "temporal_status": temporal_status(
                        e.get("dates") or e.get("dates_text"), as_of)}
    return {"index": None, "entry": None,
            "reason": "no_experience_names_the_anchor_employer"
                      if exps else "no_experience_entries",
            "temporal_status": "unknown"}


def profile_is_empty(profile) -> bool:
    p = profile if isinstance(profile, dict) else {}
    if not p:
        return True
    return not any([str(p.get("headline") or "").strip(),
                    list(p.get("current_experiences") or []),
                    str(p.get("corroborating_text") or "").strip(),
                    str(p.get("about") or "").strip(),
                    str(p.get("prior_role") or "").strip()])


# --------------------------------------------------------------- identities
_STATE = re.compile(r"\b([A-Z]{2})\b")


def locality_equal(a, b):
    """Same observed city/state? None when a side carries no locality."""
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return None
    sa, sb = _STATE.findall(str(a or "")), _STATE.findall(str(b or ""))
    if sa and sb and sa[-1] != sb[-1]:
        return False
    ca = na.split(",")[0].strip()
    cb = nb.split(",")[0].strip()
    if not ca or not cb:
        return None
    return ca == cb


_ID_PATTERNS = {
    "npi": re.compile(r"\bnpi[:\s#]*([0-9]{10})\b", re.I),
    "fdid": re.compile(r"\bfdid[:\s#]*([0-9A-Z\-]{3,})\b", re.I),
    "ein": re.compile(r"\bein[:\s#]*([0-9\-]{9,10})\b", re.I),
    "phone": re.compile(r"\bphone[:\s]*(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})",
                        re.I),
}


def identifier_compare(text_a, text_b):
    """One comparison PER identifier type; agreement and conflict are separate.

    A different phone number is not a different NPI: the caller decides what a
    weak identifier is worth. This only reports what was comparable.
    """
    agree, conflict, comparable = [], [], False
    for kind, pat in _ID_PATTERNS.items():
        va = [m.group(1).strip() for m in pat.finditer(str(text_a or ""))]
        vb = [m.group(1).strip() for m in pat.finditer(str(text_b or ""))]
        if kind == "phone":
            va = [_digits(v) for v in va if len(_digits(v)) == 10]
            vb = [_digits(v) for v in vb if len(_digits(v)) == 10]
        if not va or not vb:
            continue
        comparable = True
        if set(va) & set(vb):
            agree.append(kind)
        else:
            conflict.append(kind)
    return {"agree": agree, "conflict": conflict, "comparable": comparable}


def names_equal(a, b):
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return None
    return na == nb


# ---------------------------------------------------------------- name spans
_TITLE_WORDS = (r"chief|deputy chief|assistant chief|captain|lieutenant|"
                r"director|manager|president|mayor|marshal|commissioner|"
                r"superintendent|administrator|coordinator|supervisor")
#: the title alternation is case-insensitive; the NAME is not — folding case
#: over the whole pattern made every lowercase word a candidate surname.
_NAME_SPAN = re.compile(
    r"(?i:" + _TITLE_WORDS + r")\s+"
    # NB: the repeated group must NOT swallow a sentence-ending period, or
    # "Chief Pat Example. Chief ..." extracts "Pat Example. Chief".
    r"((?:[A-Z]\.\s+|[A-Z][a-z'\-]+\s+){1,2}[A-Z][a-z'\-]+)")
_BARE_NAME = re.compile(r"\b([A-Z][a-z'\-]{1,}\s+[A-Z][a-z'\-]{1,})\b")


def name_spans(text, max_spans=8):
    """Candidate holder names, extracted in CODE, bounded and deduplicated.

    The review's finding #6: v1's role-holder arm could not emit a name at all
    because `_fill_open_value()` only knew about domains. A judge cannot be
    asked to extract; it can be asked to choose between extracted spans.
    """
    body = str(text or "")
    if not body.strip():
        return []
    out = []
    for m in _NAME_SPAN.finditer(body):
        cand = re.sub(r"\s+", " ", m.group(1)).strip()
        if cand and cand not in out:
            out.append(cand)
        if len(out) >= int(max_spans):
            return out
    if not out:
        for m in _BARE_NAME.finditer(body):
            cand = m.group(1).strip()
            if cand and cand not in out:
                out.append(cand)
            if len(out) >= int(max_spans):
                break
    return out[: int(max_spans)]


# ==========================================================================
# v3 — the temporal gate and the identity-scope facts
#
# Everything below is ADDITIVE. `temporal_status`, `select_experience`,
# `locality_equal` and `identifier_compare` above are untouched on purpose:
# phase 1b's v1 and v2 answers are graded by replaying those exact functions,
# and a replay that silently re-computes is not a replay.
# ==========================================================================

#: v3's status vocabulary. `weak_current` is the state phase 1b had no word
#: for: the provider shows a start and no end, which is not a dated claim of
#: currentness and is not nothing either.
TEMPORAL_STATUSES_V3 = ("current", "weak_current", "ended", "future", "unknown")

_ONGOING_TAIL = ("present", "current", "now", "to date", "ongoing", "date")
_ENDED_WORDS = ("until", "till", "ended", "former", "formerly", "left",
                "departed", "retired", "resigned", "through ", "thru ",
                "no longer", "past role", "previously")
_SINCE = re.compile(r"\bsince\b", re.I)


def _ends_ongoing(text) -> bool:
    """Does the date string END in an ongoing marker?

    `endswith`, not `in`: "present" inside "presented at" is not a date, and
    "2015 - 2019 (I was present)" is not a current role.
    """
    t = re.sub(r"[\s.,;:)\]\-–—]+$", "", str(text or "").strip().lower())
    return any(t.endswith(w) for w in _ONGOING_TAIL)


def _has_end_year(text, today) -> bool:
    years = [int(m.group(0)) for m in _YEAR.finditer(str(text or ""))]
    return len(years) >= 2 and years[-1] <= today.year


def temporal_status_v3(dates_text, as_of=None, provider_current=None,
                       has_end_date=None):
    """current | weak_current | ended | future | unknown.

    Three signals, in the order a wrong YES is cheapest to avoid:

    1. an explicit END (a closed year range, `has_end_date=True`, or ended
       wording) is `ended` and beats every currency flag — a structured flag
       and a stated end date disagreeing is not a reason to commit YES;
    2. `provider_current is True`, an ongoing TAIL on the date string, or a
       source that marks no end date (`has_end_date is False`) is `current`;
    3. `since` with nothing else is `weak_current` — the provider shows a
       start and no end, which the composer may use only when the employer and
       role tests have already passed and no review reason fired.

    Anything else is `unknown`, which routes to review, never to a NO.
    """
    text = str(dates_text or "").strip().lower()
    today = date.fromisoformat(as_of) if isinstance(as_of, str) and as_of else \
        (as_of or date.today())

    years = [int(m.group(0)) for m in _YEAR.finditer(text)]
    if years and all(y > today.year for y in years) and not _ends_ongoing(text):
        return "future"

    # 1. an explicit end wins outright
    if has_end_date is True:
        return "ended"
    if not _ends_ongoing(text):
        if _has_end_year(text, today):
            return "ended"
        if any(w in text for w in _ENDED_WORDS):
            return "ended"

    # 2. a positive currency signal
    if provider_current is True:
        return "current"
    if _ends_ongoing(text):
        return "current"
    if has_end_date is False:
        return "current"

    # 3. a start with no end
    if _SINCE.search(text) and provider_current is not False:
        return "weak_current"

    return "unknown"


def select_experience_v3(anchor_org, experiences, as_of=None):
    """`select_experience`, plus the provider's own currency signal.

    Same employer-name pick as v2 — employer, time and role must be judged on
    ONE entry — but the chosen entry's `provider_current` / `end_date` travel
    into the status instead of being discarded.
    """
    exps = [e for e in (experiences or []) if isinstance(e, dict)]
    for i, e in enumerate(exps):
        if org_names_equal(anchor_org, e.get("company")) is not True:
            continue
        provider = e.get("provider_current")
        if provider is None and "is_current" in e:
            provider = e.get("is_current")
        has_end = None
        if "end_date" in e:
            has_end = bool(str(e.get("end_date") or "").strip())
        return {"index": i, "entry": e, "reason": "employer_name_match",
                "provider_current": provider,
                "temporal_status": temporal_status_v3(
                    e.get("dates_text") or e.get("dates"), as_of,
                    provider_current=provider, has_end_date=has_end)}
    return {"index": None, "entry": None, "provider_current": None,
            "reason": "no_experience_names_the_anchor_employer"
                      if exps else "no_experience_entries",
            "temporal_status": "unknown"}


# ------------------------------------------------- identity at entity scope
_URL_IN_TEXT = re.compile(r"https?://[^\s,;)\"'<>]+|\bwww\.[^\s,;)\"'<>]+",
                          re.I)


def urls_in(text):
    return [m.group(0) for m in _URL_IN_TEXT.finditer(str(text or ""))]


def shared_registrable_domains(text_a, text_b):
    """Registrable domains that BOTH records publish, sorted.

    The pair's own evidence, not an outside list: two records that both point
    at `contosomusic.example` are two locations of one brand, whatever their
    cities say.
    """
    def doms(text):
        out = set()
        for u in urls_in(text):
            d = registrable_domain(u)
            if d:
                out.add(d)
        return out
    return sorted(doms(text_a) & doms(text_b))


def multi_location_same_org(text_a, text_b):
    """True / False / None — does the pair's own evidence describe ONE
    organization operating at more than one place?

    True only on a shared registrable domain. None when neither record
    publishes a domain, because "no URL" is not evidence of independence.
    """
    if shared_registrable_domains(text_a, text_b):
        return True
    if not urls_in(text_a) or not urls_in(text_b):
        return None
    return False


#: "City, ST" as these records actually write it. `locality_equal` compares
#: whole record strings, so on a pair like
#:   A: "Example Township Volunteer Fire Company — Exampleton, PA; FDID 00000."
#:   B: "the fire service featured at https://www.examplefire.example"
#: it reports False — two different strings — although B states no locality at
#: all. Measured on the bench: that reads as a locality CONTRADICTION on almost
#: every row, which is why v3 extracts the locality instead of comparing records.
#: the city is the run of CAPITALISED words immediately before the comma, so
#: "located in Toledo, OH" yields "Toledo" and not "located in Toledo".
_LOCALITY = re.compile(
    r"\b((?:[A-Z][A-Za-z.'\-]*)(?:\s+(?:[A-Z][A-Za-z.'\-]*"
    r"|de|la|las|los|of|the|upon|del))*),\s*([A-Z]{2})\b")


def observed_locality(text):
    """-> (city, STATE) lowercased city, or None when none is stated.

    None means "this record states no locality", which is not the same as
    "this record states a different one".
    """
    m = _LOCALITY.search(str(text or ""))
    if not m:
        return None
    city = norm_text(m.group(1))
    state = m.group(2)
    return (city, state) if city else None


def locality_contradiction(text_a, text_b):
    """True / False / None — is the locality a reason to doubt this pair at the
    same entity scope?

    True only when BOTH records state a locality, the two disagree, and the
    pair's own evidence does not describe one organization across sites.
    Different cities are not a contradiction for a multi-location organization,
    and a record that states no locality contradicts nothing.
    """
    la, lb = observed_locality(text_a), observed_locality(text_b)
    if la is None or lb is None:
        return None
    if la == lb:
        return False
    return multi_location_same_org(text_a, text_b) is not True
