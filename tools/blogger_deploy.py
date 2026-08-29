#!/usr/bin/env python3
"""Safe deployment of QuestTeller HTML to Blogger API v3.

Managed Blogger resources are resolved by stable Blogger resource ID first.
Exact-title matching is retained only as a fallback for older manifest entries.

Production dependencies: Python standard library only.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]

BLOGGER_API = "https://www.googleapis.com/blogger/v3"
TOKEN_URI = "https://oauth2.googleapis.com/token"

ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class ManagedResource:
    """One Blogger post or page managed by the local repository."""

    kind: str
    path: str
    title: str
    resource_id: str | None = None


class BloggerApiError(RuntimeError):
    """Raised when the Blogger or OAuth API returns an error."""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def load_manifest(path: Path) -> tuple[str, list[ManagedResource]]:
    """Load blogger/deploy.json."""

    if not path.exists():
        raise RuntimeError(f"Manifest not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in manifest {path}: {exc}") from exc

    blog_url = str(data.get("blog_url", "")).strip()

    if not blog_url:
        raise RuntimeError("Manifest is missing 'blog_url'.")

    raw_resources = data.get("resources")

    if not isinstance(raw_resources, list):
        raise RuntimeError("Manifest 'resources' must be a list.")

    resources: list[ManagedResource] = []

    for index, item in enumerate(raw_resources, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"Manifest resource #{index} must be a JSON object."
            )

        kind = str(item.get("type", "")).strip()
        resource_path = str(item.get("path", "")).strip().replace("\\", "/")
        title = str(item.get("title", "")).strip()

        raw_id = item.get("id")
        resource_id = str(raw_id).strip() if raw_id is not None else None

        if kind not in {"post", "page"}:
            raise RuntimeError(
                f"Manifest resource #{index} has unsupported type: {kind!r}"
            )

        if not resource_path:
            raise RuntimeError(
                f"Manifest resource #{index} is missing 'path'."
            )

        if not title:
            raise RuntimeError(
                f"Manifest resource #{index} is missing 'title'."
            )

        if resource_id == "":
            resource_id = None

        if resource_id is not None and not resource_id.isdigit():
            raise RuntimeError(
                f"Manifest resource {resource_path!r} has invalid Blogger "
                f"id {resource_id!r}; expected digits only."
            )

        resources.append(
            ManagedResource(
                kind=kind,
                path=resource_path,
                title=title,
                resource_id=resource_id,
            )
        )

    return blog_url, resources


# ---------------------------------------------------------------------------
# Environment / OAuth
# ---------------------------------------------------------------------------


def required_env(name: str) -> str:
    """Return a required environment variable."""

    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}"
        )

    return value


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform an HTTP request and decode its JSON response."""

    request_headers = {
        "Accept": "application/json",
    }

    if headers:
        request_headers.update(headers)

    payload: bytes | None = None

    if form is not None:
        payload = urlencode(form).encode("utf-8")
        request_headers["Content-Type"] = (
            "application/x-www-form-urlencoded"
        )

    elif body is not None:
        payload = json.dumps(
            body,
            ensure_ascii=False,
        ).encode("utf-8")

        request_headers["Content-Type"] = (
            "application/json; charset=utf-8"
        )

    request = Request(
        url,
        data=payload,
        headers=request_headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")

    except HTTPError as exc:
        detail = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        raise BloggerApiError(
            f"HTTP {exc.code} for {method} {url}: {detail}"
        ) from exc

    except URLError as exc:
        raise BloggerApiError(
            f"Network error for {method} {url}: {exc}"
        ) from exc

    if not raw:
        return {}

    try:
        return json.loads(raw)

    except json.JSONDecodeError as exc:
        raise BloggerApiError(
            f"Non-JSON response from {url}: {raw[:500]}"
        ) from exc


def access_token() -> str:
    """Exchange the stored refresh token for a short-lived access token."""

    response = http_json(
        TOKEN_URI,
        method="POST",
        form={
            "client_id": required_env("BLOGGER_CLIENT_ID"),
            "client_secret": required_env(
                "BLOGGER_CLIENT_SECRET"
            ),
            "refresh_token": required_env(
                "BLOGGER_REFRESH_TOKEN"
            ),
            "grant_type": "refresh_token",
        },
    )

    token = response.get("access_token")

    if not token:
        raise BloggerApiError(
            "OAuth token refresh did not return access_token: "
            f"{response}"
        )

    return str(token)


# ---------------------------------------------------------------------------
# Blogger API
# ---------------------------------------------------------------------------


def api_call(
    token: str,
    path: str,
    *,
    method: str = "GET",
    params: dict[str, str | int | bool] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call Blogger API v3."""

    query = urlencode(params or {})

    url = (
        f"{BLOGGER_API}{path}"
        + (f"?{query}" if query else "")
    )

    return http_json(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
        },
        body=body,
    )


def resolve_blog(
    token: str,
    blog_url: str,
) -> dict[str, Any]:
    """Resolve the Blogger blog ID from its public URL."""

    return api_call(
        token,
        "/blogs/byurl",
        params={
            "url": blog_url,
        },
    )


def list_all(
    token: str,
    blog_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    """Return all Blogger posts or pages."""

    if kind not in {"posts", "pages"}:
        raise ValueError(
            f"Unsupported Blogger collection: {kind}"
        )

    items: list[dict[str, Any]] = []

    page_token: str | None = None

    max_results = 500 if kind == "posts" else 50

    while True:
        params: dict[str, str | int | bool] = {
            "fetchBodies": "false",
            "maxResults": max_results,
        }

        if page_token:
            params["pageToken"] = page_token

        response = api_call(
            token,
            f"/blogs/{quote(blog_id)}/{kind}",
            params=params,
        )

        response_items = response.get("items", [])

        if isinstance(response_items, list):
            items.extend(response_items)

        page_token = response.get("nextPageToken")

        if not page_token:
            return items


# ---------------------------------------------------------------------------
# Resource resolution
# ---------------------------------------------------------------------------


def resolve_remote(
    items: list[dict[str, Any]],
    managed: ManagedResource,
) -> dict[str, Any]:
    """Resolve a manifest resource against live Blogger data.

    Resolution order:

    1. Stable Blogger resource ID.
    2. Exact title match, only when no ID exists in the manifest.

    The function deliberately refuses ambiguous matches.
    """

    if managed.resource_id:
        id_matches = [
            item
            for item in items
            if str(item.get("id", "")) == managed.resource_id
        ]

        if len(id_matches) == 1:
            return id_matches[0]

        if len(id_matches) > 1:
            raise RuntimeError(
                f"Multiple Blogger {managed.kind}s matched id "
                f"{managed.resource_id!r} for "
                f"{managed.path!r}; refusing to continue."
            )

        raise RuntimeError(
            f"No Blogger {managed.kind} found with id "
            f"{managed.resource_id!r} for "
            f"{managed.path!r}."
        )

    # Backward-compatible fallback for manifests without IDs.
    title_matches = [
        item
        for item in items
        if item.get("title") == managed.title
    ]

    if len(title_matches) == 1:
        print(
            "WARNING: manifest resource has no Blogger ID; "
            f"falling back to exact-title lookup: "
            f"{managed.path}",
            file=sys.stderr,
        )

        return title_matches[0]

    available_titles = [
        str(item.get("title", ""))
        for item in items
    ]

    suggestions = difflib.get_close_matches(
        managed.title,
        available_titles,
        n=5,
        cutoff=0.35,
    )

    if not title_matches:
        suggestion_text = (
            f" Closest live titles: {suggestions}"
            if suggestions
            else ""
        )

        raise RuntimeError(
            f"No Blogger {managed.kind} matches exact title "
            f"{managed.title!r} for "
            f"{managed.path!r}."
            f"{suggestion_text}"
        )

    raise RuntimeError(
        f"Multiple Blogger {managed.kind}s match title "
        f"{managed.title!r} for "
        f"{managed.path!r}; refusing to guess."
    )


# ---------------------------------------------------------------------------
# Git change detection
# ---------------------------------------------------------------------------


def changed_paths(
    base_sha: str | None,
) -> set[str] | None:
    """Return paths changed between a Git commit and HEAD.

    None means deploy all managed resources.
    """

    if not base_sha or base_sha == ZERO_SHA:
        return None

    try:
        output = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                base_sha,
                "HEAD",
                "--",
            ],
            cwd=ROOT,
            text=True,
            stderr=subprocess.STDOUT,
        )

    except subprocess.CalledProcessError as exc:
        print(
            f"Warning: could not diff from {base_sha}; "
            "deploying all managed content.\n"
            f"{exc.output}",
            file=sys.stderr,
        )

        return None

    return {
        line.strip().replace("\\", "/")
        for line in output.splitlines()
        if line.strip()
    }


def select_resources(
    resources: list[ManagedResource],
    changed: set[str] | None,
) -> list[ManagedResource]:
    """Select only resources whose source file changed."""

    if changed is None:
        return resources

    return [
        resource
        for resource in resources
        if resource.path in changed
    ]


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def inventory(
    token: str,
    blog_id: str,
) -> None:
    """Print Blogger posts and pages without modifying anything."""

    print("POSTS")

    posts = list_all(
        token,
        blog_id,
        "posts",
    )

    for item in sorted(
        posts,
        key=lambda i: i.get("published", ""),
    ):
        print(
            f"{item.get('id')}\t"
            f"{item.get('status')}\t"
            f"{item.get('title')}\t"
            f"{item.get('url', '')}"
        )

    print("\nPAGES")

    pages = list_all(
        token,
        blog_id,
        "pages",
    )

    for item in sorted(
        pages,
        key=lambda i: i.get("title", ""),
    ):
        print(
            f"{item.get('id')}\t"
            f"{item.get('status')}\t"
            f"{item.get('title')}\t"
            f"{item.get('url', '')}"
        )


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------


def deploy_one(
    token: str,
    blog_id: str,
    managed: ManagedResource,
    remote: dict[str, Any],
) -> dict[str, Any]:
    """PATCH one existing Blogger resource."""

    source_path = ROOT / managed.path

    if not source_path.exists():
        raise RuntimeError(
            f"Managed source file does not exist: "
            f"{managed.path}"
        )

    content = source_path.read_text(
        encoding="utf-8"
    )

    # Intentionally patch only title and HTML body.
    #
    # Labels, publication date and other Blogger metadata are
    # omitted so Blogger preserves the existing values.
    body = {
        "title": managed.title,
        "content": content,
    }

    remote_id = remote.get("id")

    if not remote_id:
        raise RuntimeError(
            f"Resolved Blogger {managed.kind} has no id: "
            f"{managed.path}"
        )

    rid = quote(
        str(remote_id),
        safe="",
    )

    bid = quote(
        blog_id,
        safe="",
    )

    if managed.kind == "post":
        return api_call(
            token,
            f"/blogs/{bid}/posts/{rid}",
            method="PATCH",
            params={
                "publish": "true",
                "fetchBody": "false",
            },
            body=body,
        )

    if managed.kind == "page":
        return api_call(
            token,
            f"/blogs/{bid}/pages/{rid}",
            method="PATCH",
            params={
                "publish": "true",
            },
            body=body,
        )

    raise RuntimeError(
        f"Unsupported resource type: "
        f"{managed.kind}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate mappings and deploy QuestTeller "
            "HTML to Blogger."
        )
    )

    parser.add_argument(
        "command",
        choices=[
            "inventory",
            "check",
            "deploy",
        ],
    )

    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "blogger/deploy.json",
        help=(
            "Path to the Blogger deployment manifest. "
            "Default: blogger/deploy.json"
        ),
    )

    parser.add_argument(
        "--changed-from",
        help=(
            "Deploy/check only managed files changed "
            "between this Git SHA and HEAD."
        ),
    )

    args = parser.parse_args()

    blog_url, resources = load_manifest(
        args.manifest
    )

    token = access_token()

    blog = resolve_blog(
        token,
        blog_url,
    )

    blog_id = str(
        blog["id"]
    )

    print(
        "Blogger target: "
        f"{blog.get('name', '(unnamed)')} "
        f"[{blog_id}] "
        f"{blog_url}"
    )

    # Inventory is always read-only and ignores Git change
    # selection.
    if args.command == "inventory":
        inventory(
            token,
            blog_id,
        )

        return 0

    changed = changed_paths(
        args.changed_from
    )

    selected = select_resources(
        resources,
        changed,
    )

    if not selected:
        print(
            "No managed Blogger content changed; "
            "nothing to deploy."
        )

        return 0

    posts = list_all(
        token,
        blog_id,
        "posts",
    )

    pages = list_all(
        token,
        blog_id,
        "pages",
    )

    resolved: list[
        tuple[
            ManagedResource,
            dict[str, Any],
        ]
    ] = []

    for managed in selected:
        pool = (
            posts
            if managed.kind == "post"
            else pages
        )

        remote = resolve_remote(
            pool,
            managed,
        )

        resolved.append(
            (
                managed,
                remote,
            )
        )

        resolution_method = (
            f"id={managed.resource_id}"
            if managed.resource_id
            else "exact-title fallback"
        )

        print(
            f"Mapped {managed.kind}: "
            f"{managed.path} -> "
            f"id={remote.get('id')} "
            f"title={remote.get('title')!r} "
            f"via {resolution_method}"
        )

    # `check` ends here and makes zero writes.
    if args.command == "check":
        print(
            f"Mapping check OK for "
            f"{len(resolved)} resource(s). "
            "No changes made."
        )

        return 0

    # Only the explicit `deploy` command reaches writes.
    for managed, remote in resolved:
        result = deploy_one(
            token,
            blog_id,
            managed,
            remote,
        )

        status = result.get(
            "status",
            "UNKNOWN",
        )

        url = result.get(
            "url",
            remote.get(
                "url",
                "",
            ),
        )

        print(
            f"DEPLOYED {managed.kind}: "
            f"{managed.title} -> "
            f"{status} "
            f"{url}"
        )

    print(
        "Blogger deployment complete: "
        f"{len(resolved)} resource(s) updated."
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(
            main()
        )

    except (
        RuntimeError,
        BloggerApiError,
    ) as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        raise SystemExit(2)