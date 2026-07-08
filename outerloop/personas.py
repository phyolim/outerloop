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
its repo_path. No matching persona = no preamble — behavior is unchanged."""

import fnmatch

from . import config

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
    """-1 = excluded, 0 = generalist (no projects declared), 1 = explicit match."""
    if not patterns:
        return 0
    targets = [str(_field(ticket, k) or "").lower() for k in ("project", "repo_path")]
    if targets[1]:  # repo_path is a canonical URL; also match its bare repo name
        targets.append(targets[1].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
    for pat in patterns:
        if any(t and fnmatch.fnmatch(t, pat) for t in targets):
            return 1
    return -1


def resolve(role, ticket):
    """The persona this role should embody for this ticket, or None. A persona whose
    projects name the ticket's project/repo beats a generalist; within a rank the
    first by filename wins (deterministic, legible)."""
    if ticket is None:
        return None
    best, best_rank = None, -1
    for p in roster():  # .get: hub-inherited dicts may come from a different version
        if not p.get("body"):
            continue
        if p.get("roles") and role.lower() not in p["roles"]:
            continue
        rank = _project_rank(p.get("projects"), ticket)
        if rank > best_rank:
            best, best_rank = p, rank
    return best


def preamble(persona):
    """What gets prepended to the role prompt. Verbatim body under a header, so the
    recorded agent_run.prompt shows exactly what the agent was told to be."""
    return (f"YOUR PERSONA ({persona.get('name', 'unnamed')}) — stay in this capacity"
            f" for the whole task:\n{persona['body']}\n\n")
