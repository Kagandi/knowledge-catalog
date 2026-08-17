from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from aws_reference_agent.tools.context import (
    clear_wiki_state,
    get_wiki_state,
    set_wiki_state,
)
from aws_reference_agent.tools.wiki_tools import list_wiki_pages, read_wiki_page
from aws_reference_agent.wiki.reader import WikiError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path("/fake/scripts")
_WIKI_ROOT = Path("/fake/bu-wikis")
_WIKI_DIR = Path("/fake/bu-wikis/bu-clients/my-slug")
_SLUG = "my-slug"


def _manifest(*paths: str) -> dict:
    return {p: {"bytes": 100, "modified": "2024-01-01T00:00:00+00:00"} for p in paths}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    clear_wiki_state()


def _setup(
    manifest: dict | None = None,
    max_files: int = 10,
    max_bytes: int = 40 * 1024,
) -> None:
    set_wiki_state(
        _WIKI_ROOT,
        _SCRIPTS_DIR,
        _SLUG,
        _WIKI_DIR,
        manifest or _manifest("wiki/page.md"),
        max_files=max_files,
        max_bytes=max_bytes,
    )


# ---------------------------------------------------------------------------
# list_wiki_pages
# ---------------------------------------------------------------------------


def test_list_wiki_pages_returns_sorted_manifest(tmp_path):
    _setup(manifest=_manifest("wiki/b.md", "wiki/a.md"))
    result = list_wiki_pages()
    assert [p["path"] for p in result["pages"]] == ["wiki/a.md", "wiki/b.md"]
    assert result["count"] == 2
    assert result["slug"] == _SLUG
    assert result["wiki_dir"] == str(_WIKI_DIR)
    assert result["max_files_budget"] == 10


def test_list_wiki_pages_includes_bytes_and_modified(tmp_path):
    _setup()
    result = list_wiki_pages()
    page = result["pages"][0]
    assert "bytes" in page
    assert "modified" in page


# ---------------------------------------------------------------------------
# read_wiki_page
# ---------------------------------------------------------------------------


def test_read_wiki_page_rejects_path_not_in_manifest():
    _setup()
    result = read_wiki_page("wiki/missing.md")
    assert "not in the wiki page manifest" in result["error"]
    assert result["path"] == "wiki/missing.md"
    assert result["read_count"] == 0
    assert result["max_files_budget"] == 10


def test_read_wiki_page_rejects_already_read_path():
    _setup()
    get_wiki_state().read.add("wiki/page.md")
    get_wiki_state().read_count = 1

    result = read_wiki_page("wiki/page.md")

    assert "already read" in result["error"]
    assert result["read_count"] == 1


def test_read_wiki_page_rejects_when_budget_exhausted():
    _setup(manifest=_manifest("wiki/a.md", "wiki/b.md"), max_files=1)
    state = get_wiki_state()
    state.read.add("wiki/a.md")
    state.read_count = 1

    result = read_wiki_page("wiki/b.md")

    assert "max_files reached" in result["error"]


def test_read_wiki_page_happy_path_increments_count():
    _setup()
    fake_page = {
        "rel_path": "wiki/page.md",
        "title": "My Page",
        "markdown": "# My Page\ncontent",
        "bytes": 100,
        "modified": "2024-01-01T00:00:00+00:00",
        "truncated": False,
    }
    with patch(
        "aws_reference_agent.tools.wiki_tools.fetch_page", return_value=fake_page
    ):
        result = read_wiki_page("wiki/page.md")

    assert "error" not in result
    assert result["title"] == "My Page"
    assert result["read_count"] == 1
    assert result["max_files_budget"] == 10
    assert get_wiki_state().read == {"wiki/page.md"}


def test_read_wiki_page_surfaces_wiki_error_as_rejection():
    _setup()
    with patch(
        "aws_reference_agent.tools.wiki_tools.fetch_page",
        side_effect=WikiError("script failed"),
    ):
        result = read_wiki_page("wiki/page.md")

    assert "read failed" in result["error"]
    assert "script failed" in result["error"]
    # Count must not have been incremented.
    assert get_wiki_state().read_count == 0


def test_rejection_shape_carries_budget_context():
    _setup(max_files=7)
    result = read_wiki_page("wiki/nonexistent.md")
    assert result["path"] == "wiki/nonexistent.md"
    assert result["read_count"] == 0
    assert result["max_files_budget"] == 7
