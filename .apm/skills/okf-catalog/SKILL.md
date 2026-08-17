---
name: okf-catalog
description: Read and write OKF catalog bundles in this repo (okf/bundles/<db>/) correctly. Use whenever a task touches concept documents (tables, metrics, policies, computations, skills, attesters) — inspecting a bundle, adding a new concept, or updating an existing one.
---

# OKF Catalog Access

## Never edit concept `.md` files directly

Every concept document under `okf/bundles/<db>/` is mediated by
`okf/src/aws_reference_agent/tools/bundle_tools.py`. Use its functions, not a
raw file write:

- `read_existing_doc(concept_id)` — returns `{frontmatter, body}` for the
  concept if a document already exists, else `None`. Call this before
  writing to refine prior content instead of overwriting it.
- `write_concept_doc(concept_id, frontmatter, body)` — the only supported
  write path. It enforces, and will refuse the write (returning `{error,
  concept_id}`) rather than silently coercing bad input, on any of:
  - missing required frontmatter (`type` at minimum; see
    `bundle/document.py`'s `REQUIRED_FRONTMATTER_KEYS`)
  - body links to a concept outside the run's expected scope, or that
    escapes the bundle root
  - (source-table docs only, when query verification is on) SQL in
    `# Common query patterns` referencing a column not listed in `# Schema`
  - on an augmenting pass: minting a new concept outside `references/`, or
    shrinking an existing source-table doc's `# Schema` field set or
    `sources` list instead of adding to it

`concept_id` is the slash-joined path relative to the bundle root, e.g.
`tables/orders` (no `.md` suffix).

`write_concept_doc` fills in `generated: {by, at}` automatically if omitted —
leave it unset rather than inventing a timestamp/model name.

## Discovering what to read or write

- `list_concepts()` — every concept the active source advertises, each with
  `id`, `type`, `resource`, `hint`, `in_scope`. An entry with `in_scope:
  false` exists for recognition only (e.g. the other side of a join) — never
  link to it; name it in prose instead.
- `read_concept_raw(concept_id)` — raw structured metadata from the source
  system (Glue schema, Cube definition, etc.), before any document exists.
- `sample_rows(concept_id, n)` — small row sample from the underlying asset,
  where sampling is supported.
- `validate_query(sql)` — validates a SQL snippet against Athena; a no-op
  unless query verification is explicitly enabled for the run.

## Which tools are actually available depends on the run type

`agent.py` builds a different tool subset per ingestion source
(`build_source_options`, `build_web_options`, `build_git_options`,
`build_cube_options`, `build_docs_options`). All of them include
`list_concepts`, `read_concept_raw`, `read_existing_doc`, and
`write_concept_doc`; the rest (`sample_rows`/`validate_query`, `fetch_url`,
`search_repo`/`list_repo_files`/`read_repo_file`, `list_cubes`/
`read_cube_meta`, `list_local_docs`/`read_local_doc`) vary by source. Don't
assume a tool is present — check what's actually exposed in the current run
before relying on it.

## Document shape and bundle layout

A concept document is YAML frontmatter + a markdown body, parsed by
`bundle/document.py`. Frontmatter fields seen in practice: `type`,
`resource`, `title`, `description`, `tags`, `status`, `generated`,
`verified`, `stale_after`, `sources`, `usage_window`. `pii` is a proposed
OKF v0.3 extension referenced in the catalog's design doc — it is not yet
implemented by `write_concept_doc`, so don't invent enforcement for it.

Each database gets its own bundle at `okf/bundles/<db>/`, split by concept
category — observed subdirectories: `tables/`, `metrics/`, `policies/`,
`computations/`, `skills/`, `attesters/`. Each subdirectory has its own
`index.md`. Body sections follow the OKF convention: prose description, then
`# Schema` and `# Common query patterns` where applicable.

## Out of scope for this skill

This skill covers reading/writing today's tool-mediated bundle. It does not
cover the catalog's PR-based write-back path or CI schema validation —
neither is built yet (tracked in the project's design doc, not here). It
also doesn't cover general research-wiki access (`bu-wikis` /
`wiki-builder`) — that's a separate, deliberately deferred concern.
