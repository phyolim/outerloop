"""Agent personas: CLAUDE.md-style files that give each pipeline role a hireable
identity — expertise, voice, and the projects it specializes in — so the fleet is
managed like a team roster instead of one anonymous model per role.

One markdown file per persona under the hub's data dir (config.AGENTS_DIR — user
config, so a brew upgrade can't wipe it; prompts/agents/ in the repo holds the docs
and a copyable template). Frontmatter declares WHERE the persona applies (roles,
projects, optional model tier); the body is the personality, prepended verbatim to
the role prompt. Like worker capabilities and model routing, the roster is
HUB-owned: the hub loads its local files and ships them to workers in every
heartbeat cfg, so an edit on the hub applies fleet-wide without a release.

File format (<AGENTS_DIR>/fintech-ux.md):

    ---
    name: fintech-ux
    description: senior product engineer for financial apps
    roles: author, fixer
    projects: banking-*, acme/ledger
    model: opus
    ---
    You are a senior product engineer who has shipped consumer fintech...

Omitted `roles` = plays any role; omitted `projects` = generalist (matches any
ticket, but loses to a persona whose projects match explicitly). `projects` patterns
are fnmatch globs tested case-insensitively against the ticket's project label AND
its repo_path. No matching persona = no preamble — behavior is unchanged.

On top of the roster sits STAFFING (staffing.yml in the data dir, also hub-owned):
explicit per-project role assignments, edited from the Projects page. Resolution
precedence, deterministic and one-sentence explainable:

    project staffing  >  persona project-glob match  >  generalist  >  stock prompt

staffing.yml is a two-level map (this module reads/writes the subset it needs —
no YAML dependency):

    banking-app:
      author: fintech-ux
      reviewer: security-hawk
"""

import fnmatch

from . import config

# The staffing matrix's role slots (the coding pipeline). Other roles (knowledge,
# ops, triage, scorer) still honor staffing entries if written by hand.
STAFF_ROLES = ["groomer", "author", "reviewer", "fixer", "shipper"]

# Loader cache keyed by the (path, mtime) signature of the roster directory, so the
# hub can serve personas in every heartbeat without re-parsing unchanged files.
_cache = {"sig": None, "personas": []}


def _parse_list(value):
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse(path):
    """One persona file -> dict, or None for a file with an empty body (a persona
    with nothing to say can only dilute the role prompt)."""
    text = path.read_text()
    meta, body = {}, text
    if text.startswith("---"):
        head, sep, rest = text[3:].partition("\n---")
        if sep:
            # rest begins with the remainder of the closing fence line; drop it.
            body = rest.split("\n", 1)[1] if "\n" in rest else ""
            for line in head.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
    body = body.strip()
    if not body:
        return None
    return {
        "name": meta.get("name") or path.stem,
        "description": meta.get("description", ""),
        "roles": [r.lower() for r in _parse_list(meta.get("roles", ""))],
        "projects": [p.lower() for p in _parse_list(meta.get("projects", ""))],
        "model": meta.get("model", ""),
        "body": body,
        "file": path.name,  # so the UI editor can address the file
    }


def load_personas():
    """The local roster, sorted by filename so ties resolve deterministically.
    README and _-prefixed files are documentation, not personas."""
    d = config.AGENTS_DIR
    files = sorted(p for p in d.glob("*.md")
                   if p.name != "README.md" and not p.name.startswith("_")) if d.is_dir() else []
    sig = tuple((str(p), p.stat().st_mtime_ns) for p in files)
    if sig != _cache["sig"]:
        parsed = []
        for f in files:
            # A bad file (encoding, permissions) skips that persona, never breaks
            # the heartbeat this loader feeds.
            try:
                p = _parse(f)
            except OSError as e:
                print(f"personas: skipping {f.name}: {e}")
                p = None
            except UnicodeDecodeError as e:
                print(f"personas: skipping {f.name}: {e}")
                p = None
            if p:
                parsed.append(p)
        _cache["personas"], _cache["sig"] = parsed, sig
    return _cache["personas"]


def roster():
    """The roster in effect on THIS node: hub-inherited once a heartbeat delivered
    one (even an empty one — the hub owns the roster), else the local files."""
    if config.HUB_PERSONAS is not None:
        return config.HUB_PERSONAS
    return load_personas()


def _field(ticket, key):
    try:  # ticket is a sqlite3.Row on the hub, a plain dict on a worker
        return ticket[key]
    except (KeyError, IndexError):
        return None


def _project_rank(patterns, ticket):
    """(rank, matched_pattern): rank -1 = excluded, 0 = generalist (no projects
    declared), 1 = explicit match."""
    if not patterns:
        return 0, None
    targets = [str(_field(ticket, k) or "").lower() for k in ("project", "repo_path")]
    if targets[1]:  # repo_path is a canonical URL; also match its bare repo name
        targets.append(targets[1].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
    for pat in patterns:
        if any(t and fnmatch.fnmatch(t, pat) for t in targets):
            return 1, pat
    return -1, None


# --- staffing map (project -> role -> persona name) --------------------------

_staffing_cache = {"sig": None, "map": {}}


def load_staffing():
    """Parse staffing.yml -> {project: {role: persona_name}} (mtime-cached; the hub
    serves this in every heartbeat). Projects keyed lowercase for matching; the file
    keeps whatever case the user wrote."""
    f = config.STAFFING_FILE
    try:
        sig = f.stat().st_mtime_ns
    except OSError:
        _staffing_cache["sig"], _staffing_cache["map"] = None, {}
        return {}
    if sig == _staffing_cache["sig"]:
        return _staffing_cache["map"]
    out, project = {}, None
    try:
        lines = f.read_text().splitlines()
    except (OSError, UnicodeDecodeError) as e:
        print(f"staffing: unreadable {f.name}: {e}")
        lines = []
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        indented = ln[:1] in (" ", "\t")
        key, sep, val = ln.strip().partition(":")
        if not sep:
            continue
        key, val = key.strip().strip("'\""), val.strip().strip("'\"")
        if not indented and not val:          # "banking-app:"
            project = key.lower()
            out.setdefault(project, {})
        elif indented and project and val:    # "  author: fintech-ux"
            out[project][key.lower()] = val
    _staffing_cache["sig"], _staffing_cache["map"] = sig, out
    return out


def save_assignment(project, role, persona_name):
    """Set (or clear, with persona_name=None/'') one slot and rewrite staffing.yml
    canonically. The file is regenerated — hand-written comments do not survive."""
    m = {p: dict(rs) for p, rs in load_staffing().items()}
    project = project.strip().lower()
    slot = m.setdefault(project, {})
    if persona_name:
        slot[role.lower()] = persona_name
    else:
        slot.pop(role.lower(), None)
    if not slot:
        m.pop(project, None)
    return _write_staffing(m)


def _write_staffing(m):
    """Rewrite staffing.yml canonically from the map. Hand-written comments do not
    survive — the file is regenerated on every change."""
    lines = ["# outerloop staffing: which persona plays which role, per project.",
             "# Edited from the Projects page; regenerated on every change."]
    for p in sorted(m):
        lines.append(f"{p}:")
        lines += [f"  {r}: {m[p][r]}" for r in sorted(m[p])]
    config.ensure_dirs()
    config.STAFFING_FILE.write_text("\n".join(lines) + "\n")
    _staffing_cache["sig"] = None  # next load re-reads
    return m


def rename_project(old, new):
    """Move a project's staffing slots to a new key (Projects-page rename). No-op —
    and no file rewrite — if the project has no staffing entries."""
    m = {p: dict(rs) for p, rs in load_staffing().items()}
    slots = m.pop(old.strip().lower(), None)
    if slots is None:
        return m
    new = new.strip().lower()
    m[new] = {**m.get(new, {}), **slots}   # merge if the target already had staffing
    return _write_staffing(m)


def delete_project(name):
    """Drop a project's staffing slots (Projects-page delete). No-op if none exist."""
    m = {p: dict(rs) for p, rs in load_staffing().items()}
    if m.pop(name.strip().lower(), None) is None:
        return m
    return _write_staffing(m)


def staffing_map():
    """Staffing in effect on THIS node: hub-inherited once delivered, else local."""
    if config.HUB_STAFFING is not None:
        return config.HUB_STAFFING
    return load_staffing()


def by_name(name):
    for p in roster():
        if p.get("name") == name:
            return p
    return None


def resolve(role, ticket):
    """(persona, why) for this role + ticket — persona is None when nothing applies.
    Precedence: project staffing > persona project-glob > generalist. `why` is the
    one-sentence explanation the audit log and the Projects page both show."""
    if ticket is None:
        return None, "no ticket context"
    proj = str(_field(ticket, "project") or "").strip().lower()
    if proj:
        assigned = (staffing_map().get(proj) or {}).get(role.lower())
        if assigned:
            p = by_name(assigned)
            if p and p.get("body"):
                return p, f"project staffing: {proj}"
            # A staffing entry naming a missing persona falls through to the roster —
            # a stale assignment must not silently strip the role of any persona.
    best, best_rank, best_why = None, -1, ""
    for p in roster():  # .get: hub-inherited dicts may come from a different version
        if not p.get("body"):
            continue
        if p.get("roles") and role.lower() not in p["roles"]:
            continue
        rank, pat = _project_rank(p.get("projects"), ticket)
        if rank > best_rank:
            best, best_rank = p, rank
            best_why = f"persona glob {pat}" if pat else "generalist"
    return best, (best_why or "no persona")


def preamble(persona):
    """What gets prepended to the role prompt. Verbatim body under a header, so the
    recorded agent_run.prompt shows exactly what the agent was told to be."""
    return (f"YOUR PERSONA ({persona.get('name', 'unnamed')}) — stay in this capacity"
            f" for the whole task:\n{persona['body']}\n\n")
