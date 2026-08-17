"""Tests for aws_reference_agent.wiki.reader.

All tests are hermetic -- no network access and no real wiki-builder scripts.
Shell invocations are mocked via monkeypatch or subprocess mock, except for
`discover`, which uses tmp_path directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aws_reference_agent.wiki.reader import (
    WikiError,
    _run_query_wiki,
    check_status,
    discover,
    fetch_page,
    resolve_dir,
)


# ---------------------------------------------------------------------------
# _run_query_wiki
# ---------------------------------------------------------------------------


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_run_query_wiki_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _completed(0, stdout="hello\n")
    )
    code, out = _run_query_wiki(
        tmp_path, "query_wiki.sh", ["list", "my-slug", "--local", "--agent"],
        wiki_root=tmp_path,
    )
    assert code == 0
    assert out == "hello\n"


def test_run_query_wiki_timeout_raises_wiki_error(tmp_path, monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="query_wiki.sh", timeout=30)

    monkeypatch.setattr(subprocess, "run", _raise)
    with pytest.raises(WikiError, match="timed out"):
        _run_query_wiki(tmp_path, "query_wiki.sh", [], wiki_root=tmp_path)


def test_run_query_wiki_file_not_found_raises_wiki_error(tmp_path, monkeypatch):
    def _raise(*a, **kw):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(subprocess, "run", _raise)
    with pytest.raises(WikiError, match="script not found"):
        _run_query_wiki(tmp_path, "query_wiki.sh", [], wiki_root=tmp_path)


def test_run_query_wiki_non_zero_exit_raises_wiki_error_with_stderr_tail(
    tmp_path, monkeypatch
):
    long_stderr = "x" * 600
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _completed(1, stderr=long_stderr),
    )
    with pytest.raises(WikiError) as exc_info:
        _run_query_wiki(tmp_path, "query_wiki.sh", [], wiki_root=tmp_path)
    # stderr tail is capped at 500 chars
    assert "x" * 500 in str(exc_info.value)
    assert "x" * 501 not in str(exc_info.value)


def test_run_query_wiki_ok_codes_allows_non_zero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _completed(1, stdout="ok")
    )
    code, out = _run_query_wiki(
        tmp_path, "query_wiki.sh", [], wiki_root=tmp_path, ok_codes=(0, 1)
    )
    assert code == 1
    assert out == "ok"


# ---------------------------------------------------------------------------
# check_status
# ---------------------------------------------------------------------------


def _mock_status_output(content_present: bool) -> str:
    flag = "true" if content_present else "false"
    return (
        f"wiki_root=/tmp/bu-wikis\n"
        f"current_branch=main\n"
        f"target_branch=wiki/my-slug\n"
        f"content_present={flag}\n"
    )


def test_check_status_parses_content_present_true(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _completed(0, stdout=_mock_status_output(True)),
    )
    result = check_status(tmp_path, tmp_path, "my-slug")
    assert result["content_present"] is True
    assert result["current_branch"] == "main"
    assert result["wiki_root"] == "/tmp/bu-wikis"


def test_check_status_parses_content_present_false(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _completed(0, stdout=_mock_status_output(False)),
    )
    result = check_status(tmp_path, tmp_path, "my-slug")
    assert result["content_present"] is False


def test_check_status_value_with_equals_sign_is_not_split_further(
    tmp_path, monkeypatch
):
    # A value that itself contains "=" must still parse correctly.
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _completed(0, stdout="key=a=b\n"),
    )
    result = check_status(tmp_path, tmp_path, "slug")
    assert result["key"] == "a=b"


def test_check_status_invokes_list_subcommand(tmp_path, monkeypatch):
    # Regression guard: query_wiki.sh's usage is
    # `query_wiki.sh list <slug> --local --agent` — the subcommand is
    # positional and consumed first (`SUBCOMMAND="$1"; shift`). Omitting
    # "list" makes the script treat the slug itself as the subcommand and
    # fail with "Unknown subcommand", which no test caught previously
    # because nothing asserted on the actual argv passed to subprocess.run.
    captured = {}

    def _fake_run(argv, **kw):
        captured["argv"] = argv
        return _completed(0, stdout=_mock_status_output(True))

    monkeypatch.setattr(subprocess, "run", _fake_run)
    check_status(tmp_path, tmp_path, "my-slug")
    script_path, *rest = captured["argv"]
    assert script_path == str(tmp_path / "query_wiki.sh")
    assert rest == ["list", "my-slug", "--local", "--agent"]


# ---------------------------------------------------------------------------
# resolve_dir
# ---------------------------------------------------------------------------


def test_resolve_dir_invokes_checkout_script_with_agent_flag(tmp_path, monkeypatch):
    captured = {}

    def _fake_run(argv, **kw):
        captured["argv"] = argv
        return _completed(0, stdout=f"{tmp_path}/bu-clients/my-slug\n")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = resolve_dir(tmp_path, tmp_path, "my-slug")
    script_path, *rest = captured["argv"]
    assert script_path == str(tmp_path / "checkout_wiki_local.sh")
    assert rest == ["my-slug", "--agent"]
    assert result == Path(f"{tmp_path}/bu-clients/my-slug")


def test_resolve_dir_raises_on_non_zero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _completed(1, stderr="registry and local clone have diverged"),
    )
    with pytest.raises(WikiError, match="diverged"):
        resolve_dir(tmp_path, tmp_path, "my-slug")


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def _make_wiki_dir(root: Path, files: dict[str, str]) -> Path:
    """Create a wiki dir at root with the given files."""
    wiki_dir = root / "my-wiki"
    for rel, content in files.items():
        path = wiki_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return wiki_dir


def test_discover_finds_markdown_pages_under_wiki_subdir(tmp_path):
    wiki_dir = _make_wiki_dir(
        tmp_path,
        {
            "wiki/intro.md": "# Intro",
            "wiki/schema/tables.md": "# Tables",
        },
    )
    result = discover(wiki_dir)
    assert set(result["manifest"].keys()) == {
        "wiki/intro.md",
        "wiki/schema/tables.md",
    }
    assert result["truncated_count"] == 0


def test_discover_skips_raw_derived_logs_dirs(tmp_path):
    wiki_dir = _make_wiki_dir(
        tmp_path,
        {
            "wiki/good.md": "good",
            "wiki/raw/data.md": "raw",
            "wiki/derived/agg.md": "derived",
            "wiki/logs/run.md": "log",
        },
    )
    result = discover(wiki_dir)
    assert list(result["manifest"].keys()) == ["wiki/good.md"]


def test_discover_skips_root_level_metadata_files(tmp_path):
    wiki_dir = _make_wiki_dir(
        tmp_path,
        {
            "wiki/wiki.config.md": "config",
            "wiki/sources.md": "sources",
            "wiki/real.md": "real",
        },
    )
    result = discover(wiki_dir)
    assert list(result["manifest"].keys()) == ["wiki/real.md"]


def test_discover_truncates_to_max_files(tmp_path):
    files = {f"wiki/page{i:03d}.md": f"# Page {i}" for i in range(10)}
    wiki_dir = _make_wiki_dir(tmp_path, files)
    result = discover(wiki_dir, max_files=3)
    assert len(result["manifest"]) == 3
    assert result["truncated_count"] == 7


def test_discover_returns_empty_manifest_when_wiki_subdir_absent(tmp_path):
    wiki_dir = tmp_path / "empty-wiki"
    wiki_dir.mkdir()
    result = discover(wiki_dir)
    assert result["manifest"] == {}
    assert result["truncated_count"] == 0


def test_discover_manifest_entries_have_bytes_and_modified(tmp_path):
    wiki_dir = _make_wiki_dir(tmp_path, {"wiki/page.md": "# Page\ncontent"})
    result = discover(wiki_dir)
    entry = result["manifest"]["wiki/page.md"]
    assert isinstance(entry["bytes"], int)
    assert entry["bytes"] > 0
    # ISO-8601 UTC string contains a "T"
    assert "T" in entry["modified"]


# ---------------------------------------------------------------------------
# fetch_page
# ---------------------------------------------------------------------------


def test_fetch_page_computes_path_relative_to_wiki_root(tmp_path, monkeypatch):
    wiki_root = tmp_path
    wiki_dir = _make_wiki_dir(wiki_root, {"wiki/orders.md": "# Orders\nSchema notes."})
    captured = {}

    def _fake_run(argv, **kw):
        captured["argv"] = argv
        return _completed(0, stdout="# Orders\nSchema notes.")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    result = fetch_page(
        tmp_path, wiki_root, wiki_dir, "my-slug", "wiki/orders.md"
    )
    script_path, *rest = captured["argv"]
    assert script_path == str(tmp_path / "query_wiki.sh")
    # fetch's <path> arg is relative to WIKI_ROOT, i.e. wiki_dir's own
    # relative path (here "my-wiki") joined with the manifest-relative page path.
    expected_path = (wiki_dir.relative_to(wiki_root) / "wiki/orders.md").as_posix()
    assert rest == ["fetch", "my-slug", expected_path, "--local", "--agent"]
    assert result["rel_path"] == "wiki/orders.md"
    assert result["title"] == "Orders"
    assert result["markdown"] == "# Orders\nSchema notes."
    assert result["truncated"] is False


def test_fetch_page_truncates_to_max_bytes(tmp_path, monkeypatch):
    wiki_root = tmp_path
    wiki_dir = _make_wiki_dir(wiki_root, {"wiki/big.md": "x" * 100})
    long_content = "y" * 100
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _completed(0, stdout=long_content)
    )
    result = fetch_page(
        tmp_path, wiki_root, wiki_dir, "my-slug", "wiki/big.md", max_bytes=10
    )
    assert result["truncated"] is True
    assert result["bytes"] == 100
    assert len(result["markdown"].encode("utf-8")) <= 10 + len("\n\n[...truncated...]")


def test_fetch_page_raises_on_non_zero_exit(tmp_path, monkeypatch):
    wiki_root = tmp_path
    wiki_dir = _make_wiki_dir(wiki_root, {"wiki/missing.md": "x"})
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _completed(1, stderr="no such file")
    )
    with pytest.raises(WikiError, match="no such file"):
        fetch_page(tmp_path, wiki_root, wiki_dir, "my-slug", "wiki/missing.md")
