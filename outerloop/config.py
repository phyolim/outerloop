"""Process-wide configuration. All knobs in one place; runtime state lives under
data/ (gitignored). FAKE mode lets the whole loop run with canned agents and a
simulated git/gh so the orchestration spine is verifiable with no external deps."""

import json
import os
import shutil
import uuid
from pathlib import Path

# Repo root = parent of this package dir. Runtime state is rooted at data/ unless
# OUTERLOOP_HOME overrides it.
REPO_ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("OUTERLOOP_HOME", REPO_ROOT / "data")).resolve()

DB_PATH = HOME / "inbox.db"
SCHEMA_PATH = REPO_ROOT / "schema.sql"
PROMPTS_DIR = REPO_ROOT / "prompts"
AGENTS_DIR = HOME / "agents"        # persona roster (user config — survives upgrades)
STAFFING_FILE = HOME / "staffing.yml"  # per-project role->persona assignments
WORKTREES_DIR = HOME / "worktrees"
REPOS_DIR = HOME / "repos"          # clones of repos this orchestrator created itself
ARTIFACTS_DIR = HOME / "artifacts"
BACKUPS_DIR = HOME / "backups"
UI_DIST = REPO_ROOT / "ui" / "dist"   # built React SPA; served at / when present
KILL_FILE = HOME / "KILL"
# Machine-local runtime config the menu-bar app writes (e.g. the hub URL). Read at
# runtime so a worker's hub can change with no rebuild — settings, not build-time.
SETTINGS_FILE = HOME / "settings.json"


def local_setting(key, default=None):
    try:
        return json.loads(SETTINGS_FILE.read_text()).get(key, default)
    except (FileNotFoundError, ValueError):
        return default


def set_local(key, value):
    """Write one machine-local runtime setting to settings.json (the store the worker
    and menu-bar app read via local_setting). Read-modify-write; creates the file."""
    ensure_dirs()
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (FileNotFoundError, ValueError):
        data = {}
    data[key] = value
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))

# A box that declares itself a hub (role=hub) runs REAL by default — a hub is the fleet's
# real-mode owner. Everything else stays FAKE-safe until real mode is turned on. Precedence:
# OUTERLOOP_FAKE (env wins) > `outerloop local fake 0/1` > role default (hub=real, else fake).
FAKE = (os.environ.get("OUTERLOOP_FAKE") or local_setting("fake")
        or ("0" if local_setting("role") in ("hub", "both") else "1")) != "0"

# checks_green() default-DENIES a merge when a repo has NO CI configured ("no CI" is
# not "green" — must-fix #3). Set OUTERLOOP_ALLOW_MERGE_WITHOUT_CI=1 to permit merging such
# a repo anyway (e.g. a personal repo with no GitHub Actions). This ONLY relaxes the
# no-CI case; a repo whose checks are actually failing still blocks the merge.
ALLOW_MERGE_WITHOUT_CI = os.environ.get("OUTERLOOP_ALLOW_MERGE_WITHOUT_CI") == "1"

# Caps (mirrored into the settings table on init; settings rows win at runtime).
LEASE_TTL_MIN = 30          # > longest single stage (an author run)
LOCK_STALE_SEC = 90         # a tick whose heartbeat is older than this is presumed crashed
HEARTBEAT_SEC = 20          # how often a live tick refreshes its heartbeat
MAX_REVIEW_ROUNDS = 3       # reviewing<->fixing hard cap, then escalate
MAX_CLARIFICATIONS = 3      # author may ask the human this many questions before proceeding
MAX_ATTEMPTS = 12           # per-ticket global stage-entry ceiling -> failed
MAX_CONSEC_TIMEOUTS = 2     # consecutive agent timeouts on a ticket -> failed
MAX_PR_CREATE_ATTEMPTS = 3  # shipper runs before a failing PR-create gives up (deterministic errs)
MAX_REVIEW_DIFF_CHARS = 100_000  # diffs larger than this gate to a human, never auto-review
MAX_TICKETS_PER_TICK = 3
# Budgets are in TOKENS (input + cache_creation + output; cache READS excluded — they
# are near-free and would swamp the signal). USD is legacy: on a subscription every
# run reports $0, so dollar ceilings never trigger. The per-run bound is the
# wall-clock timeout below; a killed run is charged TIMEOUT_CHARGE_TOKENS worst-case
# so the ceilings still engage.
TIMEOUT_CHARGE_TOKENS = 200_000
TICK_BUDGET_TOKENS = 500_000     # halt selection once a tick consumes this much
TICKET_BUDGET_TOKENS = 1_000_000 # cumulative per-ticket ceiling -> failed
AGENT_TIMEOUT_SEC = 900     # subprocess wall-clock wall for a headless claude run
# Cheap classify/estimate roles run inline on the hub scheduler thread — a hung call
# must not stall decision resumes for the full 15-minute wall.
AGENT_TIMEOUT_BY_ROLE = {"triage": 60, "scorer": 60, "shipper": 300, "warmup": 180}

# Fleet (hub-and-spoke) timings.
WORKER_OFFLINE_SEC = 120    # fleet view marks a worker offline after this heartbeat gap
SCHED_INTERVAL_SEC = 3      # hub scheduler cadence (DB-only top-half)
WORKER_POLL_SEC = 2         # worker poll interval when idle
WORKER_REAP_SEC = 600       # how often a remote worker sweeps its worktrees via /api/reap_check
FLEET_BUDGET_TOKENS = 5_000_000  # hub-wide token ceiling across ALL workers in the window
FLEET_SPEND_WINDOW_HOURS = 24
PIN_OFFLINE_PARK_HOURS = 24 # park an active ticket pinned to a worker unseen this long
# Capabilities seeded for a newly registered worker (combined node's co-located worker,
# LAN-paired workers). An empty cap set only matches no-requirement tickets, so a fresh
# worker would sit idle; this mirrors the broad default a hand-provisioned worker gets.
# Hub-owned once set (never re-clobbered) — edit live on the Fleet page.
DEFAULT_CAPS = ["dev", "repos:*", "heavy"]

# Claude Code self-installs to a per-user ~/.local/bin, added to PATH by an interactive
# shell rc — launchd (cron, brew services, a bare `.pkg`-less `outerloop service`) never
# sources that rc, so `which` alone finds nothing there. Mirrors the fallback list
# deploy/mac/scripts/preflight.sh already uses so the two checks agree.
_CLAUDE_FALLBACKS = (
    Path.home() / ".local/bin/claude",
    Path.home() / ".claude/local/claude",
    Path.home() / ".npm-global/bin/claude",
    Path.home() / ".bun/bin/claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
)
# gh/git need the same treatment: a brew-installed gh lives in /opt/homebrew/bin,
# which launchd's default PATH (/usr/bin:/bin:/usr/sbin:/sbin) does not include.
_GH_FALLBACKS = (Path("/opt/homebrew/bin/gh"), Path("/usr/local/bin/gh"))
_GIT_FALLBACKS = (Path("/usr/bin/git"), Path("/opt/homebrew/bin/git"),
                  Path("/usr/local/bin/git"))


def _find_bin(env_var, setting_key, name, fallbacks=()):
    """Absolute binary path resolution: env var (cron/launchd-baked) > settings.json
    (persisted by `doctor`, e.g. on a brew install with no baked env) > PATH > known
    install locations `which` can't see under a stripped launchd PATH > bare name
    (lets a clear FileNotFoundError surface instead of silently no-op-ing)."""
    return (os.environ.get(env_var)
            or local_setting(setting_key)
            or shutil.which(name)
            or next((str(p) for p in fallbacks if p.exists()), None)
            or name)


# Absolute binary paths (cron/launchd env is minimal). Fall back to PATH lookup, then
# to settings.json / known install locations.
CLAUDE_BIN = _find_bin("OUTERLOOP_CLAUDE_BIN", "claude_bin", "claude", _CLAUDE_FALLBACKS)
GH_BIN = _find_bin("OUTERLOOP_GH_BIN", "gh_bin", "gh", _GH_FALLBACKS)
GIT_BIN = _find_bin("OUTERLOOP_GIT_BIN", "git_bin", "git", _GIT_FALLBACKS)

# Per-role model tiers: cheap models for classify/estimate, capable ones for deep
# coding. Tune the defaults here; override per-runner via env (see resolve_model).
MODEL_TIERS = {
    "haiku": "claude-haiku-4-5-20251001",   # cheapest/fastest — trivial classification
    "sonnet": "claude-sonnet-4-6",          # balanced — grooming, review, drafting
    "opus": "claude-opus-4-8",              # most capable — deep coding / architecture
}
ROLE_MODEL_DEFAULTS = {
    "triage": "haiku",      # is this junk? — one-word call
    "scorer": "haiku",      # rate four 1..5 factors
    "groomer": "sonnet",    # expand into tasks + acceptance criteria
    "reviewer": "sonnet",   # review a diff (bump to opus for high-stakes repos)
    "shipper": "haiku",     # push the branch + open the PR — two shell commands
    "knowledge": "sonnet",  # research/write a deliverable
    "ops": "sonnet",        # draft an external action
    "author": "opus",       # write the code — the deep-work role
    "fixer": "sonnet",      # address enumerated findings in an existing worktree — scoped
                            # work sonnet handles; override OUTERLOOP_MODELS=fixer=opus to revert
    "warmup": "haiku",      # one-time permission warm-up — echo + write a file
}
DEFAULT_MODEL_TIER = "sonnet"


def _models_map():
    """Parse OUTERLOOP_MODELS='author=opus reviewer=opus triage=haiku' into a dict. This is
    the single env var a runner (worker/hub) bakes to override several roles at once."""
    out = {}
    for pair in (os.environ.get("OUTERLOOP_MODELS") or "").split():
        if "=" in pair:
            r, m = pair.split("=", 1)
            out[r.strip().lower()] = m.strip()
    return out


def resolve_model(role, persona_model=None):
    """Model id for an agent role. A persona's model wins outright: the roster and
    the role routing are both hub-owned operator intent, and the persona (role +
    project) is the more specific of the two — also, the hub ships MODELS fully
    resolved, so on a worker role routing can't be told apart from role defaults.
    Below that, hub-inherited routing (HUB_MODELS, set by apply_hub_cfg on workers,
    empty on the hub) then local env (the hub itself / before the first heartbeat):
       persona model (prompts/agents frontmatter)  >  OUTERLOOP_MODEL_<ROLE>
       >  OUTERLOOP_MODELS[role]  >  OUTERLOOP_MODEL (all roles)  >  role default.
    A value may be a tier alias ('haiku'/'sonnet'/'opus') or a full model id."""
    override = (HUB_MODELS.get(role.lower())
                or os.environ.get(f"OUTERLOOP_MODEL_{role.upper()}")
                or _models_map().get(role.lower())
                or os.environ.get("OUTERLOOP_MODEL"))
    tier_or_id = persona_model or override or ROLE_MODEL_DEFAULTS.get(role, DEFAULT_MODEL_TIER)
    return MODEL_TIERS.get(tier_or_id, tier_or_id)  # alias -> id, else pass a full id through


# Fleet-behavior knobs are HUB-owned (like worker capabilities): a worker inherits
# them from every heartbeat, so one Mac's stray OUTERLOOP_FAKE=1 can't run a fake
# lifecycle inside a real fleet. Machine-local config (paths, binaries, identity)
# is never inherited. HUB_MODELS wins over local model env once populated.
# HUB_PERSONAS / HUB_STAFFING are None until a heartbeat delivers them (None =
# "no hub spoke yet, use local files"; []/{}= "the hub says empty" — they differ).
HUB_MODELS = {}
HUB_PERSONAS = None
HUB_STAFFING = None


def hub_cfg():
    """What the hub advertises in a heartbeat response (its own resolved values)."""
    from . import personas  # late import: personas needs config's paths at module load
    return {"FAKE": FAKE, "ALLOW_MERGE_WITHOUT_CI": ALLOW_MERGE_WITHOUT_CI,
            "MODELS": {r: resolve_model(r) for r in ROLE_MODEL_DEFAULTS},
            "PERSONAS": personas.load_personas(),
            "STAFFING": personas.load_staffing()}


def apply_hub_cfg(cfg):
    """Worker side: overwrite fleet-behavior knobs with the hub's. No-op when the
    hub predates this (no 'cfg' in the heartbeat) — local env keeps applying."""
    global FAKE, ALLOW_MERGE_WITHOUT_CI, HUB_MODELS, HUB_PERSONAS, HUB_STAFFING
    if not cfg:
        return
    FAKE = bool(cfg.get("FAKE", FAKE))
    ALLOW_MERGE_WITHOUT_CI = bool(cfg.get("ALLOW_MERGE_WITHOUT_CI", ALLOW_MERGE_WITHOUT_CI))
    HUB_MODELS = cfg.get("MODELS") or {}
    if "PERSONAS" in cfg:  # an old hub omits the key — keep using local files
        HUB_PERSONAS = cfg["PERSONAS"]
    if "STAFFING" in cfg:
        HUB_STAFFING = cfg["STAFFING"]


# One uuid per process. Combined with a fresh heartbeat it is the real authority
# on "is the lease holder still alive" — os.kill(pid,0) alone is unsound (PID reuse).
BOOT_UUID = uuid.uuid4().hex

SETTINGS_DEFAULTS = {
    "kill_switch": "off",
    "lease_ttl_min": str(LEASE_TTL_MIN),
    "lock_stale_sec": str(LOCK_STALE_SEC),
    "max_review_rounds": str(MAX_REVIEW_ROUNDS),
    "max_attempts": str(MAX_ATTEMPTS),
    "max_tickets_per_tick": str(MAX_TICKETS_PER_TICK),
    "tick_budget_tokens": str(TICK_BUDGET_TOKENS),
    "ticket_budget_tokens": str(TICKET_BUDGET_TOKENS),
    "fleet_budget_tokens": str(FLEET_BUDGET_TOKENS),
    "notify_url": "",     # ntfy-style push target for decision-queue items ("" = off)
    "intake_token": "",   # shared secret for POST /api/intake ("" = endpoint disabled)
}


def ensure_dirs():
    for d in (HOME, WORKTREES_DIR, REPOS_DIR, ARTIFACTS_DIR, BACKUPS_DIR, AGENTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
