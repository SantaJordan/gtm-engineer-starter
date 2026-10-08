#!/usr/bin/env python3
"""The FIVE decisions this harness judges, and what counts as an answer.

One Decision object per decision carries: the commit vocabulary, the tokens
that mean "I am not answering", whether the vocabulary is open (a domain, a
person's name) or closed (a label), which committed values are ACCEPTS (a
positive claim, so a wrong one is a false accept), and the equivalence rule
used to grade a commit.

Equivalence follows the internal benchmark's scoring doctrine:
  - domains compare by REGISTRABLE domain, never by string tricks
    (the lstrip('www.') bug class),
  - person names fold case/punctuation, middle initials and generational
    suffixes, and nothing else is ever inferred,
  - sentinels (NO_WEBSITE / UNKNOWN) match only their own sentinel,
  - categorical labels compare case-folded and exact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from . import repo

# ---------------------------------------------------------------- normalizing
_fetch_cache = repo.try_import("lib.fetch_cache", repo.CRAWFORD_SCRIPTS)

_THREE_PART = ("oh.us", "mi.us", "ny.us", "ca.us", "nc.us", "pa.us", "il.us")


def registrable_domain(text: str) -> str:
    """Apex of a URL or bare domain; '' when there isn't one.

    Borrows crawford's stdlib `registered_domain` (it knows the US locality
    namespace: ci.exampleton.oh.us -> exampleton.oh.us) and falls back to a
    last-two-labels rule when that module cannot be imported.
    """
    s = str(text or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    s = s.split("@")[-1].split(":", 1)[0].strip(".")
    if not s or " " in s:
        return ""
    if _fetch_cache is not None:
        try:
            return _fetch_cache.registered_domain(s)
        except Exception:
            pass
    parts = s.split(".")
    if len(parts) <= 2:
        return s
    if len(parts) >= 4 and ".".join(parts[-3:]) in _THREE_PART:
        return ".".join(parts[-4:])
    return ".".join(parts[-2:])


def norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower()).strip(" .\"'`")


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(text: str) -> str:
    """Person-name equivalence (score_board.py §norm_name, same rules)."""
    t = re.sub(r"[.,'\"`]", " ", str(text or "").lower())
    toks = [x for x in t.split() if x]
    toks = [x for x in toks if x not in _NAME_SUFFIXES]
    if len(toks) > 2:
        toks = [toks[0]] + [x for x in toks[1:-1] if len(x) > 1] + [toks[-1]]
    return " ".join(toks)


def norm_sentinel(text: str, sentinels: Sequence[str]) -> Optional[str]:
    s = re.sub(r"[\s\-]+", "_", str(text or "").strip().upper()).strip("._")
    return s if s in {x.upper() for x in sentinels} else None


# ------------------------------------------------------------------ decisions
SENTINELS = ("NO_WEBSITE", "UNKNOWN", "NOT_FOUND")

#: the one token every arm may emit to mean "the frozen evidence cannot settle
#: this" — deliberately shared across all five decisions.
EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"


def _domain_equivalent(answer: str, truth: str) -> bool:
    sa = norm_sentinel(answer, ("NO_WEBSITE",))
    st = norm_sentinel(truth, ("NO_WEBSITE",))
    if sa is not None or st is not None:
        return sa == st
    ra, rt = registrable_domain(answer), registrable_domain(truth)
    return bool(ra) and ra == rt


def _name_equivalent(answer: str, truth: str) -> bool:
    sa = norm_sentinel(answer, ("UNKNOWN",))
    st = norm_sentinel(truth, ("UNKNOWN",))
    if sa is not None or st is not None:
        return sa == st
    return bool(norm_name(answer)) and norm_name(answer) == norm_name(truth)


def _categorical_equivalent(answer: str, truth: str) -> bool:
    return bool(norm_text(answer)) and norm_text(answer) == norm_text(truth)


@dataclass(frozen=True)
class Decision:
    name: str
    subject: str                     # what is being decided about
    vocab: tuple                     # closed commit vocabulary ('' = open)
    abstain_tokens: tuple
    accept_values: tuple             # committed values that ASSERT something
    answer_kind: str                 # domain | name | categorical
    open_vocab: bool
    question: str
    _equivalent: Callable[[str, str], bool] = field(repr=False,
                                                    default=_categorical_equivalent)

    # -- semantics ---------------------------------------------------------
    def is_abstain(self, verdict) -> bool:
        if verdict is None:
            return True
        v = re.sub(r"[\s\-]+", "_", str(verdict).strip().upper()).strip("._")
        if not v:
            return True
        return v in {x.upper() for x in self.abstain_tokens}

    def is_accept(self, verdict) -> bool:
        """Is this commit a positive claim? (a wrong one is a false accept)"""
        if self.is_abstain(verdict):
            return False
        if self.open_vocab:
            # an open vocabulary asserts whenever it names a thing; the
            # absence sentinels are the non-asserting commits
            return norm_sentinel(verdict, SENTINELS) is None
        return norm_text(verdict) in {norm_text(v) for v in self.accept_values}

    def is_reject(self, verdict) -> bool:
        """Is this commit a claim that the thing is NOT there?

        The mirror of is_accept, and worth its own name: claiming a department
        has no website, or that a person does not work somewhere, is the error
        that silently shrinks a list. It is a commit, not an abstention.
        """
        if self.is_abstain(verdict):
            return False
        return not self.is_accept(verdict)

    def equivalent(self, answer, truth) -> bool:
        if answer is None or truth is None:
            return False
        return self._equivalent(str(answer), str(truth))


DECISIONS = {
    "same_identity": Decision(
        name="same_identity",
        subject="two records (organizations or people)",
        vocab=("yes", "no"),
        abstain_tokens=("UNSURE", "ABSTAIN", EVIDENCE_INSUFFICIENT, "UNKNOWN"),
        accept_values=("yes",),
        answer_kind="categorical",
        open_vocab=False,
        question="Do records A and B refer to the same entity?",
        _equivalent=_categorical_equivalent,
    ),
    "real_website": Decision(
        name="real_website",
        subject="an organization and a candidate domain",
        vocab=(),                    # open: any registrable domain, or NO_WEBSITE
        abstain_tokens=("ABSTAIN", EVIDENCE_INSUFFICIENT, "UNSURE"),
        accept_values=(),            # open_vocab: any named domain asserts
        answer_kind="domain",
        open_vocab=True,
        question="What is this organization's own official website today?",
        _equivalent=_domain_equivalent,
    ),
    "site_status": Decision(
        name="site_status",
        subject="one URL recorded as an organization's web address",
        vocab=("live_official", "parked_or_placeholder", "dead",
               "social_only", "different_org"),
        abstain_tokens=("ABSTAIN", EVIDENCE_INSUFFICIENT, "UNSURE", "UNKNOWN"),
        accept_values=("live_official",),
        answer_kind="categorical",
        open_vocab=False,
        question="What is this URL's current status?",
        _equivalent=_categorical_equivalent,
    ),
    "employment_now": Decision(
        name="employment_now",
        subject="a person and a company",
        vocab=("YES", "NO"),
        abstain_tokens=("UNSURE", "ABSTAIN", EVIDENCE_INSUFFICIENT, "UNKNOWN"),
        accept_values=("YES",),
        answer_kind="categorical",
        open_vocab=False,
        question="Does this person currently work at this company?",
        _equivalent=_categorical_equivalent,
    ),
    "role_holder_now": Decision(
        name="role_holder_now",
        subject="an organization and a named role",
        vocab=(),                    # open: a person's name, or UNKNOWN
        abstain_tokens=("ABSTAIN", EVIDENCE_INSUFFICIENT, "UNSURE", "UNKNOWN"),
        accept_values=(),
        answer_kind="name",
        open_vocab=True,
        question="Who holds this role at this organization today?",
        _equivalent=_name_equivalent,
    ),
}

#: benchmark question types -> decision. same-person joins same-entity:
#: both ask "are these two records one thing", and one code path grades both.
BENCH_TYPE_DECISION = {
    "same-entity": "same_identity",
    "same-person": "same_identity",
    "real-website": "real_website",
    "site-status": "site_status",
    "employment-now": "employment_now",
    "role-holder-now": "role_holder_now",
}
