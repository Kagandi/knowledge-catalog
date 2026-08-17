from __future__ import annotations

from typing import Any

from aws_reference_agent.tools.context import get_wiki_state
from aws_reference_agent.wiki.reader import WikiError, fetch_page


def list_wiki_pages() -> dict[str, Any]:
    """List the wiki pages available for ingestion in this session.

    Call this once at the start. It returns the complete set of pages you may
    read — `read_wiki_page` refuses any path not listed here, so there is no
    point guessing filenames. Use each entry's `path`, `bytes`, and `modified`
    to decide what is worth reading and in what order; `modified` is also the
    freshness signal you should record when you cite a page.

    Return shape:
      {"slug", "wiki_dir", "pages": [{"path", "bytes", "modified"}], "count",
       "max_files_budget"}
    """
    state = get_wiki_state()
    pages = [
        {"path": rel, "bytes": meta["bytes"], "modified": meta["modified"]}
        for rel, meta in sorted(state.manifest.items())
    ]
    return {
        "slug": state.slug,
        "wiki_dir": str(state.wiki_dir),
        "pages": pages,
        "count": len(pages),
        "max_files_budget": state.max_files,
    }


def read_wiki_page(path: str) -> dict[str, Any]:
    """Read one wiki page and return its content as markdown.

    `path` must be one of the paths returned by `list_wiki_pages`. The
    session-wide read budget (`max_files`) is enforced inside this tool. When a
    read is rejected the return value contains an `error` field instead of
    content. Treat that as a signal to pick a different page; do not retry the
    same path.

    Successful return shape:
      {"path", "title", "markdown", "bytes", "modified", "truncated",
       "read_count", "max_files_budget"}

    Rejected return shape:
      {"error": "<reason>", "path", "read_count", "max_files_budget"}
    """
    state = get_wiki_state()

    def _reject(reason: str) -> dict[str, Any]:
        return {
            "error": reason,
            "path": path,
            "read_count": state.read_count,
            "max_files_budget": state.max_files,
        }

    if path not in state.manifest:
        return _reject(
            "path is not in the wiki page manifest returned by list_wiki_pages"
        )
    if path in state.read:
        return _reject("already read in this session")
    if state.read_count >= state.max_files:
        return _reject("max_files reached")

    try:
        page = fetch_page(
            state.scripts_dir,
            state.wiki_root,
            state.wiki_dir,
            state.slug,
            path,
            max_bytes=state.max_bytes,
        )
    except WikiError as e:
        return _reject(f"read failed: {e}")

    state.read.add(path)
    state.read_count += 1

    return {
        "path": page["rel_path"],
        "title": page["title"],
        "markdown": page["markdown"],
        "bytes": page["bytes"],
        "modified": page["modified"],
        "truncated": page["truncated"],
        "read_count": state.read_count,
        "max_files_budget": state.max_files,
    }
