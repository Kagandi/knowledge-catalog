"""Read pages from a locally checked-out bu-wikis wiki via wiki-builder scripts.

Pure I/O with no awareness of tool state — the confinement and budget rules
live in `tools/wiki_tools.py`, exactly as in `docs/reader.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_MAX_TITLE_CHARS = 120
_ATX_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")

# Sub-directories that are not wiki pages.
_SKIP_DIRS = frozenset({"raw", "derived", "logs"})
# Root-level files that are not wiki pages.
_SKIP_ROOT_FILES = frozenset({"wiki.config.md", "sources.md"})


class WikiError(Exception):
    pass


def _run_query_wiki(
    scripts_dir: Path,
    script: str,
    args: list[str],
    *,
    wiki_root: Path,
    timeout: int = 30,
    ok_codes: tuple[int, ...] = (0,),
) -> tuple[int, str]:
    """Run a wiki-builder shell script and return (returncode, stdout).

    Never uses shell=True so arguments are never interpolated by the shell.
    Sets WIKI_ROOT in the environment so scripts that source resolve_wiki_root.sh
    pick up the caller-supplied root rather than defaulting to /tmp/bu-wikis.
    """
    env = {**os.environ, "WIKI_ROOT": str(wiki_root)}
    try:
        result = subprocess.run(
            [str(scripts_dir / script), *args],
            cwd=None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise WikiError(
            f"{script} timed out after {timeout}s"
        ) from e
    except FileNotFoundError as e:
        raise WikiError(
            f"script not found: {scripts_dir / script}"
        ) from e

    if result.returncode not in ok_codes:
        stderr_tail = result.stderr.strip()[-500:]
        raise WikiError(
            f"{script} {' '.join(args)} failed (exit {result.returncode}): {stderr_tail}"
        )

    return result.returncode, result.stdout


def check_status(
    scripts_dir: Path, wiki_root: Path, slug: str
) -> dict[str, str | bool]:
    """Return the wiki-local-status dict for `slug`.

    Runs ``query_wiki.sh list <slug> --local --agent`` and parses its
    ``key=value`` stdout. ``content_present`` is coerced to a real bool.
    """
    _, stdout = _run_query_wiki(
        scripts_dir,
        "query_wiki.sh",
        ["list", slug, "--local", "--agent"],
        wiki_root=wiki_root,
    )
    result: dict[str, str | bool] = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "content_present":
            result[key] = value.lower() == "true"
        else:
            result[key] = value
    return result


def resolve_dir(
    scripts_dir: Path, wiki_root: Path, slug: str
) -> Path:
    """Return the on-disk wiki directory for `slug`.

    Runs ``checkout_wiki_local.sh <slug> --agent`` and returns the path it
    prints to stdout.
    """
    _, stdout = _run_query_wiki(
        scripts_dir,
        "checkout_wiki_local.sh",
        [slug, "--agent"],
        wiki_root=wiki_root,
    )
    return Path(stdout.strip())


def discover(wiki_dir: Path, *, max_files: int = 200) -> dict:
    """Walk ``wiki_dir/wiki/`` and return a manifest of page files.

    Skips ``raw/``, ``derived/``, ``logs/``, and root-level ``wiki.config.md``
    / ``sources.md``. Each path is confined so it cannot escape ``wiki_dir``.
    Returns::

        {
            "manifest": {<rel_posix>: {"bytes": int, "modified": iso8601_utc_str}},
            "truncated_count": int,
        }

    where ``rel_posix`` is the POSIX path relative to ``wiki_dir``.
    """
    pages_root = wiki_dir / "wiki"
    found: list[tuple[str, Path]] = []

    if pages_root.is_dir():
        for path in pages_root.rglob("*"):
            if not path.is_file():
                continue
            # Resolve before the containment check — same guard as docs/reader.py.
            resolved = path.resolve()
            if resolved != wiki_dir and not resolved.is_relative_to(wiki_dir.resolve()):
                continue
            rel = path.relative_to(wiki_dir)
            parts = rel.parts
            # Skip if any path component is in the skip-dirs set.
            if any(p in _SKIP_DIRS for p in parts[1:]):  # parts[0] is "wiki"
                continue
            # Skip root-level metadata files.
            if len(parts) == 2 and parts[1] in _SKIP_ROOT_FILES:
                continue
            found.append((rel.as_posix(), path))

    found.sort(key=lambda x: x[0])
    kept = found[:max_files]
    truncated_count = len(found) - len(kept)

    manifest: dict[str, dict[str, object]] = {}
    for rel_posix, path in kept:
        stat = path.stat()
        modified = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds")
        manifest[rel_posix] = {"bytes": stat.st_size, "modified": modified}

    return {"manifest": manifest, "truncated_count": truncated_count}


def _extract_title(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _ATX_TITLE_RE.match(stripped)
        if match:
            return match.group(1)
        return stripped[:_MAX_TITLE_CHARS]
    return None


def _truncate(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n\n[...truncated...]"


def fetch_page(
    scripts_dir: Path,
    wiki_root: Path,
    wiki_dir: Path,
    slug: str,
    rel_path: str,
    *,
    max_bytes: int = 40 * 1024,
) -> dict:
    """Fetch one wiki page by its manifest-relative path.

    ``rel_path`` is a POSIX path relative to ``wiki_dir`` (as returned by
    ``discover``). The script receives a path relative to ``wiki_root``, which
    is computed as ``(wiki_dir.relative_to(wiki_root) / rel_path).as_posix()``.

    Return shape::

        {
            "rel_path": str,
            "title": str | None,
            "markdown": str,
            "bytes": int,
            "modified": str,   # ISO-8601 UTC
            "truncated": bool,
        }
    """
    # Path to pass to the script: relative to wiki_root.
    fetch_path = (wiki_dir.relative_to(wiki_root) / rel_path).as_posix()

    _, stdout = _run_query_wiki(
        scripts_dir,
        "query_wiki.sh",
        ["fetch", slug, fetch_path, "--local", "--agent"],
        wiki_root=wiki_root,
    )

    on_disk = (wiki_dir / rel_path)
    try:
        modified_ts = on_disk.stat().st_mtime
    except OSError:
        modified_ts = 0.0
    modified = datetime.fromtimestamp(modified_ts, tz=timezone.utc).isoformat(
        timespec="seconds"
    )

    raw_bytes = len(stdout.encode("utf-8", errors="replace"))
    markdown = _truncate(stdout, max_bytes)

    return {
        "rel_path": rel_path,
        "title": _extract_title(stdout),
        "markdown": markdown,
        "bytes": raw_bytes,
        "modified": modified,
        "truncated": raw_bytes > max_bytes,
    }
