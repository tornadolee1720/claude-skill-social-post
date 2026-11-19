#!/usr/bin/env python3
"""Import authenticated Zoho Social Threads post results into social-post's canonical JSONL store.

This is a *local* adapter: it makes no network requests and never handles API tokens.
Do not commit the input JSON or the resulting personal account data to a public repository.

Example:
    python scripts/import_zoho_threads.py --input /private/zoho-result.json \
      --expected-handle example_personal --captured-at 2026-11-20T01:45:00+08:00
    # Inspect the dry-run; add --write when ready.

Zoho response source: getSocialPublishedPosts(network='threadsbyig').
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("captured-at must be timezone-aware (ISO 8601 with UTC offset)")
    return result


def _nonnegative_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or int(value) != value:
        raise ValueError(f"invalid {name}: expected non-negative integer or null")
    return int(value)


def _normalized_handle(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing account handle")
    return value.strip().lstrip("@").casefold()


def unwrap_posts(payload: Any) -> list[dict[str, Any]]:
    """Accept a native Zoho MCP response, raw Zoho HTTP envelope, or data list.

    Missing/failed responses raise: never turn an error or empty page into a success.
    """
    if isinstance(payload, dict) and payload.get("status") == "failure":
        raise ValueError("Zoho API returned failure")
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        payload = json.loads(payload["text"])
    if isinstance(payload, dict):
        payload = payload.get("data", payload)
    if isinstance(payload, dict):
        payload = payload.get("data")
    if not isinstance(payload, list):
        raise ValueError("input does not contain a Zoho published-post list")
    if not payload:
        raise ValueError("input post list is empty; no data were imported")
    if not all(isinstance(p, dict) for p in payload):
        raise ValueError("a Zoho post is not an object")
    return payload


def _content_type(p: dict[str, Any]) -> str:
    attachments = p.get("attachments") or {}
    items = attachments.get("data") or [] if isinstance(attachments, dict) else []
    if not isinstance(items, list):
        items = []
    media = str(items[0].get("media_type", "")).lower() if items and isinstance(items[0], dict) else ""
    if media == "video":
        return "short_video"
    if media == "image":
        return "image"
    if media in ("carousel_album", "carousel"):
        return "carousel"
    return "text"


def make_bundles(payload: Any, expected_handle: str, captured_at: str) -> list[dict[str, Any]]:
    handle = _normalized_handle(expected_handle)
    measured = _time(captured_at).astimezone(timezone.utc)
    captured_at_iso = measured.isoformat()
    bundles = []
    seen_ids: set[str] = set()
    for p in unwrap_posts(payload):
        p_handle = _normalized_handle((p.get("from") or {}).get("name"))
        if p_handle != handle:
            raise ValueError(f"account mismatch: expected @{handle}, got @{p_handle}; abort all")
        native_id = str(p.get("id") or "").strip()
        if not native_id or not native_id.isdigit():
            raise ValueError("missing or invalid native Threads post ID")
        if native_id in seen_ids:
            raise ValueError(f"duplicate native Threads post ID: {native_id}")
        seen_ids.add(native_id)
        raw_time = p.get("created_time")
        if isinstance(raw_time, bool) or not isinstance(raw_time, (int, float)) or raw_time <= 0:
            raise ValueError(f"invalid created_time for post {native_id}")
        published = datetime.fromtimestamp(float(raw_time) / 1000, tz=timezone.utc)
        if measured < published:
            raise ValueError(f"captured-at predates publication: {native_id}")
        permalink = p.get("perma_link")
        if not isinstance(permalink, str) or not permalink.startswith((
            f"https://www.threads.com/@{handle}/post/",
            f"https://www.threads.net/@{handle}/post/",
        )):
            raise ValueError(f"post permalink does not belong to expected handle: {native_id}")
        post_id = f"threads:{handle}:{native_id}"
        snapshot_id = f"zoho:{handle}:{native_id}:{measured.strftime('%Y%m%dT%H%M%S%fZ')}"
        published_iso = published.isoformat()
        view_count = _nonnegative_int(p.get("view_count"), "view_count")
        metrics = {
            "views": view_count,
            "likes": _nonnegative_int(p.get("like_count"), "like_count"),
            "comments": _nonnegative_int(p.get("comment_count"), "comment_count"),
            "reposts": _nonnegative_int(p.get("retweet_count"), "retweet_count"),
            "quotes": None,
            "shares": None,
        }
        bundles.append({
            "post": {
                "post_id": post_id,
                "platforms": ["threads"],
                "published_at": published_iso,
                "caption": p.get("message") if isinstance(p.get("message"), str) else "",
                "content_type": _content_type(p),
                "permalink": permalink,
                "source_provider": "zoho_social",
                "source_account": handle,
            },
            "snapshot": {
                "snapshot_id": snapshot_id,
                "post_id": post_id,
                "platform_scope": "threads",
                "captured_at": captured_at_iso,
                "hours_since_publish": round((measured - published).total_seconds() / 3600, 4),
                "metrics": metrics,
                "metric_qualifiers": {k: "not_reported" for k in ("quotes", "shares")},
                "source_provider": "zoho_social",
                "source_account": handle,
            },
        })
    return bundles


def _stage(bundles: list[dict[str, Any]], data_dir: Path) -> tuple[dict, dict, str]:
    """Stage *all* bundles together using social-post's existing transactions."""
    from log_outcome import load_store_records, validate_staged
    from social_store import store_revision

    paths, records = load_store_records(data_dir)
    post_by_id = {p.get("post_id"): p for p in records["posts"]}
    snapshot_by_id = {s.get("snapshot_id"): s for s in records["snapshots"]}
    added_posts = added_snapshots = 0
    skipped_snapshots = 0
    for bundle in bundles:
        post = bundle["post"]
        snapshot = bundle["snapshot"]
        prior = post_by_id.get(post["post_id"])
        if prior is None:
            records["posts"].append(post)
            post_by_id[post["post_id"]] = post
            added_posts += 1
        elif (prior.get("source_account") != post["source_account"] or
              prior.get("platforms") != ["threads"] or
              prior.get("published_at") != post["published_at"]):
            raise ValueError(f"existing post identity conflict: {post['post_id']}")
        existing_snapshot = snapshot_by_id.get(snapshot["snapshot_id"])
        if existing_snapshot is None:
            records["snapshots"].append(snapshot)
            snapshot_by_id[snapshot["snapshot_id"]] = snapshot
            added_snapshots += 1
        elif existing_snapshot == snapshot:
            skipped_snapshots += 1
        else:
            raise ValueError(f"snapshot ID collision with different metrics: {snapshot['snapshot_id']}")
    staged = {paths[name]: value for name, value in records.items()}
    validate_staged(staged)
    return staged, {"new_posts": added_posts, "new_snapshots": added_snapshots,
                    "duplicate_snapshots_skipped": skipped_snapshots}, store_revision(data_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Private Zoho post-list JSON")
    parser.add_argument("--expected-handle", required=True, help="Exact personal Threads username")
    parser.add_argument("--captured-at", required=True, help="When the Zoho query was actually captured, ISO offset")
    parser.add_argument("--store-dir", type=Path, help="Optional private social-post data dir")
    parser.add_argument("--write", action="store_true", help="Commit after validation (default dry run)")
    args = parser.parse_args()
    from social_data import DATA_DIR
    from social_store import commit_records
    data_dir = args.store_dir or DATA_DIR
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    bundles = make_bundles(payload, args.expected_handle, args.captured_at)
    staged, counts, revision = _stage(bundles, data_dir)
    result = {"result": "VALIDATED", "mode": "write" if args.write else "dry_run", "source_posts":len(bundles), **counts}
    if args.write and (counts["new_posts"] or counts["new_snapshots"]):
        result["new_revision"] = commit_records(staged, data_dir=data_dir, expected_revision=revision)[:12]
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
