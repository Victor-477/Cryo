# ============================================================
#  Cryo — packages: cryo.toml and cryo.lock  (roadmap 12.3)
#
#  WHAT THIS IS, AND WHAT IT IS NOT
#
#  It is a way to depend on another Cryo package by PATH, with a lockfile that
#  pins exactly what was built, by content.
#
#  It is NOT a registry. There is no server, no publish, no version solving, no
#  network. Saying so plainly matters more than the feature: "package manager"
#  usually promises all of that, and a half-registry that silently resolves
#  something different tomorrow is worse than no registry at all.
#
#  WHY THE LOCK HASHES CONTENT
#
#  11.14 established that the same source gives a byte-identical `.pyro`. A
#  lockfile pinning a version NUMBER would add a second, weaker notion of "the
#  same build" alongside that one, and the two would eventually disagree — a
#  dependency edited in place keeps its version and changes the output.
#
#  So the lock records a digest OF THE SOURCES. Same lock means the same bytes
#  went in, which is the same guarantee 11.14 already gives, extended across a
#  dependency boundary rather than duplicated.
#
#  IMPORT SYNTAX
#
#      import "@geometry/shapes.cryo"      // from the dependency `geometry`
#      import "./local.cryo"               // as always, relative to this file
#
#  The `@` is deliberate. Without a sigil, `import "geometry/shapes.cryo"`
#  would mean a local directory to a reader and a dependency to the compiler,
#  or the other way round, depending on what happens to exist on disk — and
#  which one you got would change as files appeared and disappeared.
# ============================================================
import hashlib
import os

MANIFEST = 'cryo.toml'
LOCKFILE = 'cryo.lock'
SIGIL = '@'


class PackageError(Exception):
    pass


class Manifest:
    def __init__(self, root, name, version, deps):
        self.root = root          # directory holding cryo.toml
        self.name = name
        self.version = version
        self.deps = deps          # name -> path, relative to root

    def dep_root(self, name):
        return os.path.abspath(os.path.join(self.root, self.deps[name]))


def _load_toml(path):
    try:
        import tomllib
    except ModuleNotFoundError:                      # Python < 3.11
        raise PackageError(
            "reading cryo.toml needs Python 3.11 or newer (the standard "
            "library's tomllib). The compiler itself does not — only the "
            "package commands.")
    with open(path, 'rb') as f:
        try:
            return tomllib.load(f)
        except Exception as e:
            raise PackageError(f"{path}: {e}")


def find_manifest(start_dir):
    """The nearest cryo.toml at or above `start_dir`, or None.

    Walking UP rather than requiring the command to run at the root: a build is
    usually invoked on a file somewhere inside the package, and refusing unless
    the working directory happens to be the root is a papercut with no upside.
    """
    d = os.path.abspath(start_dir)
    while True:
        cand = os.path.join(d, MANIFEST)
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def load(start_dir):
    """The Manifest governing `start_dir`, or None when there is no package."""
    path = find_manifest(start_dir)
    if path is None:
        return None
    data = _load_toml(path)
    pkg = data.get('package') or {}
    name = pkg.get('name')
    if not name:
        raise PackageError(f"{path}: [package] has no `name`")
    deps = {}
    for dname, spec in (data.get('dependencies') or {}).items():
        if isinstance(spec, str):
            # `geometry = "../geometry"` — the short form for the only kind of
            # dependency there is.
            deps[dname] = spec
        elif isinstance(spec, dict) and 'path' in spec:
            deps[dname] = spec['path']
        else:
            raise PackageError(
                f"{path}: dependency '{dname}' must be a path — either "
                f'`{dname} = "../{dname}"` or `{dname} = {{ path = "../{dname}" }}`. '
                f"There is no registry to fetch a version from.")
    return Manifest(os.path.dirname(path), name, pkg.get('version', '0.0.0'), deps)


# ── import resolution ───────────────────────────────────────
def resolve_import(spec, importer_dir, manifest):
    """Absolute path for an `import "…"`, or None to fall back to relative.

    Only `@name/...` is claimed here. Everything else stays exactly as it was,
    so adding a cryo.toml to an existing project cannot change what its
    existing imports mean.
    """
    if not spec.startswith(SIGIL):
        return None
    body = spec[len(SIGIL):]
    dep, _, rest = body.partition('/')
    if not dep or not rest:
        raise PackageError(
            f'import "{spec}": expected "@package/file.cryo"')
    if manifest is None:
        raise PackageError(
            f'import "{spec}": there is no {MANIFEST} for this project, so '
            f"'{dep}' cannot be resolved. Create one with `cryoc pkg init`.")
    if dep not in manifest.deps:
        known = ', '.join(sorted(manifest.deps)) or '(none declared)'
        raise PackageError(
            f'import "{spec}": \'{dep}\' is not a dependency of '
            f"'{manifest.name}'. Declared: {known}")
    root = manifest.dep_root(dep)
    if not os.path.isdir(root):
        raise PackageError(
            f"dependency '{dep}' points at {root}, which does not exist")
    full = os.path.abspath(os.path.join(root, rest))
    # A dependency must not be a way to read arbitrary files: `@dep/../../x`
    # would escape the package it names.
    if os.path.commonpath([full, root]) != root:
        raise PackageError(
            f'import "{spec}": path escapes the dependency\'s directory')
    if not os.path.isfile(full):
        raise PackageError(
            f'import "{spec}": no such file in dependency \'{dep}\' ({full})')
    return full


# ── the lock ────────────────────────────────────────────────
def _iter_sources(root):
    for base, _dirs, files in os.walk(root):
        for f in sorted(files):
            if f.endswith('.cryo'):
                yield os.path.join(base, f)


def digest_of(root):
    """A digest of every .cryo under `root`, by content.

    Paths are relative and slash-normalised, and the list is sorted, so the
    same tree hashes the same on Windows and POSIX and regardless of the order
    the filesystem happens to return.
    """
    h = hashlib.sha256()
    n = 0
    for p in sorted(_iter_sources(root)):
        rel = os.path.relpath(p, root).replace(os.sep, '/')
        h.update(rel.encode('utf-8'))
        h.update(b'\0')
        with open(p, 'rb') as f:
            h.update(hashlib.sha256(f.read()).digest())
        n += 1
    return f"sha256:{h.hexdigest()}", n


def compute_lock(manifest):
    rows = []
    for dep in sorted(manifest.deps):
        root = manifest.dep_root(dep)
        if not os.path.isdir(root):
            raise PackageError(
                f"dependency '{dep}' points at {root}, which does not exist")
        d, n = digest_of(root)
        rows.append((dep, manifest.deps[dep], d, n))
    return rows


def render_lock(manifest, rows):
    out = [
        "# Generated by `cryoc pkg lock` — do not edit by hand.",
        "#",
        "# `hash` covers the CONTENT of every .cryo in the dependency, not a",
        "# version number: 11.14 already guarantees that the same sources give",
        "# a byte-identical .pyro, and pinning a version instead would add a",
        "# second, weaker notion of the same build for the two to disagree",
        "# about the first time someone edits a dependency in place.",
        "",
        "version = 1",
        f'root = "{manifest.name}"',
        "",
    ]
    for dep, path, digest, n in rows:
        out += ["[[package]]",
                f'name = "{dep}"',
                f'path = "{path.replace(os.sep, "/")}"',
                f'files = {n}',
                f'hash = "{digest}"',
                ""]
    return '\n'.join(out)


def read_lock(manifest_dir):
    path = os.path.join(manifest_dir, LOCKFILE)
    if not os.path.isfile(path):
        return None
    data = _load_toml(path)
    return {p['name']: p for p in (data.get('package') or [])}


def verify(manifest):
    """[] when the tree matches the lock, else a list of human-readable drifts."""
    locked = read_lock(manifest.root)
    if locked is None:
        return [f"no {LOCKFILE} — run `cryoc pkg lock`"]
    problems = []
    current = {dep: (d, n) for dep, _p, d, n in compute_lock(manifest)}
    for dep in sorted(set(locked) | set(current)):
        if dep not in current:
            problems.append(f"'{dep}' is in {LOCKFILE} but no longer declared "
                            f"in {MANIFEST}")
        elif dep not in locked:
            problems.append(f"'{dep}' is declared but not in {LOCKFILE}")
        elif locked[dep].get('hash') != current[dep][0]:
            problems.append(
                f"'{dep}' has changed since it was locked "
                f"({locked[dep].get('files', '?')} file(s) locked, "
                f"{current[dep][1]} now)")
    return problems
