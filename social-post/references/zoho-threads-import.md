# Zoho Social → Threads outcome adapter (opt-in, read-only source)

This workflow imports results from an **already authorized** Zoho Social account into the existing social-post canonical JSONL ledger. It does not request credentials, schedule posts or publish replies. It keeps each Threads account isolated.

## Source contract

Use the connected Zoho Social MCP operation `ZohoSocial_getSocialPublishedPosts` with `network="threadsbyig"` and the correct portal and brand. Confirm the returned `from.name` matches the account being analyzed. A Zoho **brand** name is not necessarily the Threads **account** name; a brand may contain several channels.

Export the response JSON to a private local location; do **not** commit account data, post text, personal identifiers, OAuth tokens, or insight snapshots to GitHub. Note the actual time at which the data was fetched, including timezone. If a page contains an error, stop; do not label it as zero posts. If pagination is unavailable, call the result a **partial sample** rather than a complete history.

Example (replace the fictional handle and private file path):

```bash
python social-post/scripts/import_zoho_threads.py \
  --input /private/zoho-personal-posts.json \
  --expected-handle example_personal \
  --captured-at 2026-11-20T01:45:00+08:00

# Only after reviewing dry-run results:
python social-post/scripts/import_zoho_threads.py \
  --input /private/zoho-personal-posts.json \
  --expected-handle example_personal \
  --captured-at 2026-11-20T01:45:00+08:00 --write

python social-post/scripts/social_data.py validate
```


`--captured-at` must be the original query observation time, **not** an arbitrary later file-import time. Re-importing an identical file with an identical captured-at is idempotent; a second genuine fetch should get its own timestamp and snapshot.

The tool defaults to dry-run. `--write` uses the canonical ledger's existing staging, validation and atomic optimistic commit logic. It never rewrites a prior snapshot; a conflicting duplicate snapshot ID causes an error.

## Mapping and cautions

| Zoho field | Canonical field | Semantics |
| --- | --- | --- |
| `id` | `post_id = threads:{handle}:{id}` | Stable identity, isolated by account; do not silently merge crossposted content |
| `from.name` | `source_account` | Must equal `--expected-handle` for **every** imported row |
| `created_time` | `published_at` | Milliseconds UTC timestamp, stored ISO with offset |
| `message` | `caption` | Preserve source text |
| `view_count` | `metrics.views` | Threads views; **not** video plays, impressions or reach |
| `like_count` | `metrics.likes` | Positive action count |
| `comment_count` | `metrics.comments` | Replies |
| `retweet_count` | `metrics.reposts` | Reposts, **not** native shares |
| not returned | `metrics.shares`, `metrics.quotes` | `null`, not `0` |
| query observation timestamp | `captured_at` | Immutable point-in-time source receipt |

No scheduling or publishing is included. This importer does not parse private comments/replies (only counts) and does not obtain follower growth, distribution sources, native shares, quote totals, or post-age-aligned growth curves.

## Evaluation rules

- Compare only posts of **similar age** when assessing timing experiments. Do not compare cumulative views of a new post with an old post and claim causal performance.
- Analyze format groups separately and report the number of posts in each group.
- State denominators clearly: `likes/views` differs from `likes/reach` and may be affected by repeated views.
- Snapshot time is the **observation time**, not a reconstructed historical value.
- Work from the existing `data/*.jsonl` ledger; do not create a second canonical `threads_daily_tracker.json`.
- One or two viral posts do not establish an algorithm rule. Follow the existing experiment evidence gate before promoting a rule.

## Test

```bash
python -m unittest discover -s social-post/tests -p 'test_import_zoho_threads.py' -v
```

All example fixtures use fictional usernames and numbers. No actual user data or login credentials belong in this public repository.
