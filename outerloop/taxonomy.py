"""User-facing ticket taxonomy (Feature/Bug/Chore/Research/Ops) and its mapping to the
handler-routing `type` (coding/knowledge/ops). `kind` is what the human picks and the
board shows; `type` stays the coarse routing key the handlers/scoring/model tiers use.
One place owns the relationship so creation paths and the UI never disagree."""

# kind -> handler-routing type. Order here is the UI display order.
KIND_TO_TYPE = {
    "feature":  "coding",
    "bug":      "coding",
    "chore":    "coding",
    "research": "knowledge",
    "ops":      "ops",
}
KINDS = list(KIND_TO_TYPE)
DEFAULT_KIND = "feature"

# Reverse used only to backfill legacy rows / API callers that set `type` but no `kind`.
_TYPE_TO_KIND = {"coding": "feature", "knowledge": "research", "ops": "ops"}

# Presentation + a one-line hint threaded into the groomer/author prompt. Colors reuse
# the existing UI palette (see web.py FLEET_CSS status pills).
KIND_META = {
    "feature":  {"label": "Feature",  "color": "#1a7f37",
                 "hint": "This is a new feature — build the smallest thing that satisfies the request."},
    "bug":      {"label": "Bug",      "color": "#b4400a",
                 "hint": "This is a bug fix — reproduce it with a failing test first, then make it pass."},
    "chore":    {"label": "Chore",    "color": "#0a56c2",
                 "hint": "This is a chore/maintenance task — keep the change surgical, no scope creep."},
    "research": {"label": "Research", "color": "#5b4bb3",
                 "hint": "This is research — produce a written deliverable, not code."},
    "ops":      {"label": "Ops",      "color": "#8a6d16",
                 "hint": "This is an ops action — draft it for human approval; do not execute anything."},
}


def normalize_kind(kind, type_=None):
    """A valid kind. Falls back to the legacy `type` (screener/old API) then DEFAULT_KIND."""
    if kind in KIND_TO_TYPE:
        return kind
    return _TYPE_TO_KIND.get(type_, DEFAULT_KIND)


def type_for(kind):
    return KIND_TO_TYPE.get(kind, "knowledge")


def meta(kind):
    return KIND_META.get(kind, KIND_META[DEFAULT_KIND])
