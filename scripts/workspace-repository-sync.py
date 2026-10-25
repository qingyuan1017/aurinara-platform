#!/usr/bin/env python3
"""Synchronize an authorized logical Repository working copy for code-server."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
REPOSITORY_DIR = (WORKSPACE_DIR / "repository").resolve()
STATE_DIR = (WORKSPACE_DIR / ".aurinara").resolve()
INDEX_PATH = STATE_DIR / "repository-index.json"
CONFLICT_DIR = STATE_DIR / "repository-conflicts"
READY_PATH = WORKSPACE_DIR / ".aurinara-repository-ready"
INTERVAL = int(os.environ.get("WORKSPACE_SYNC_INTERVAL_SECONDS", "60"))


class SyncError(RuntimeError):
    pass


def _request(url: str, token: str, *, method: str = "GET", data=None, headers=None):
    request_headers = {"X-Workspace-Token": token, **(headers or {})}
    request = Request(url, data=data, method=method, headers=request_headers)
    try:
        return urlopen(request, timeout=120)  # noqa: S310 - URL is injected by Platform.
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise SyncError(f"Repository request failed ({error.code}): {detail}") from error
    except URLError as error:
        raise SyncError(f"Repository request failed: {error.reason}") from error


def _json(url: str, token: str) -> dict:
    with _request(url, token) as response:
        return json.load(response)


def _safe_path(relative: str) -> Path:
    target = (REPOSITORY_DIR / relative).resolve()
    if target == REPOSITORY_DIR or REPOSITORY_DIR not in target.parents:
        raise SyncError("Repository returned an unsafe path")
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_index() -> dict[str, dict]:
    try:
        payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return payload.get("files", {}) if payload.get("version") == 1 else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_index(files: dict[str, dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = INDEX_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"version": 1, "files": files}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(INDEX_PATH)


def _conflict(relative: str, message: str) -> None:
    CONFLICT_DIR.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(relative.encode()).hexdigest()[:16] + ".md"
    (CONFLICT_DIR / name).write_text(
        f"# Repository synchronization conflict\n\nFile: `{relative}`\n\n{message}\n",
        encoding="utf-8",
    )


def _download(base_url: str, token: str, item: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".aurinara-download")
    digest = hashlib.sha256()
    with (
        _request(f"{base_url}/versions/{item['version_id']}", token) as response,
        temporary.open("wb") as output,
    ):
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    if digest.hexdigest() != item["sha256"]:
        temporary.unlink(missing_ok=True)
        raise SyncError(f"Checksum mismatch while downloading {item['path']}")
    temporary.replace(target)


def _upload(base_url: str, token: str, item: dict, path: Path, digest: str) -> dict:
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as body, _request(
        f"{base_url}/nodes/{item['node_id']}",
        token,
        method="PUT",
        data=body,
        headers={
            "Content-Type": media_type,
            "Content-Length": str(path.stat().st_size),
            "X-Base-Version-Id": item["version_id"],
            "X-Content-SHA256": digest,
        },
    ) as response:
        return json.load(response)


def synchronize(base_url: str, token: str) -> None:
    REPOSITORY_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    old = _load_index()
    conflicts: set[str] = set()

    for relative, item in old.items():
        path = _safe_path(relative)
        if not path.is_file() or not item.get("writable", False):
            continue
        digest = _sha256(path)
        if digest == item.get("sha256"):
            continue
        try:
            updated = _upload(base_url, token, item, path, digest)
            item.update(
                version_id=updated["version_id"],
                sha256=updated["sha256"],
                size_bytes=updated["size_bytes"],
                media_type=updated["media_type"],
            )
        except SyncError as error:
            conflicts.add(relative)
            _conflict(relative, str(error))

    manifest = _json(base_url, token)
    remote = {item["path"]: item for item in manifest.get("files", [])}
    next_index: dict[str, dict] = {}

    for relative, item in remote.items():
        path = _safe_path(relative)
        previous = old.get(relative)
        local_digest = _sha256(path) if path.is_file() else None
        locally_changed = bool(
            previous and local_digest is not None and local_digest != previous.get("sha256")
        )
        remotely_changed = bool(
            previous and item["version_id"] != previous.get("version_id")
        )
        if relative in conflicts or (locally_changed and remotely_changed):
            _conflict(
                relative,
                "The local working copy and central Repository both changed. "
                "Your local file was kept; reconcile it in the Repository UI before retrying.",
            )
            next_index[relative] = previous or item
            continue
        if local_digest != item["sha256"]:
            if previous is None and path.exists():
                _conflict(
                    relative,
                    "A local file already exists at the path of a new Repository file. "
                    "The local file was kept.",
                )
                continue
            _download(base_url, token, item, path)
        next_index[relative] = item

    for relative, item in old.items():
        if relative in remote:
            continue
        path = _safe_path(relative)
        if path.is_file() and _sha256(path) == item.get("sha256"):
            path.unlink()

    _save_index(next_index)
    (WORKSPACE_DIR / "README.md").write_text(
        "# Aurinara Workspace\n\n"
        "The `repository/` folder is an authorized working copy of the Aurinara "
        "Repository stored in S3/MinIO. Saving an existing writable file publishes "
        "a new immutable Repository version during synchronization.\n\n"
        "New files, renames, and deletions are kept as workspace files but are not "
        "published automatically in this first version.\n",
        encoding="utf-8",
    )
    READY_PATH.touch()


def _registration() -> tuple[str, str]:
    direct_url = os.environ.get("WORKSPACE_REPOSITORY_URL")
    direct_token = os.environ.get("WORKSPACE_CALLBACK_TOKEN")
    if direct_url and direct_token:
        return direct_url.rstrip("/"), direct_token
    callback_url = os.environ.get("WORKSPACE_CALLBACK_URL")
    if callback_url and direct_token:
        return callback_url.removesuffix("/sync") + "/repository", direct_token

    registration_url = os.environ.get("WORKSPACE_STATIC_REGISTRATION_URL")
    bootstrap = os.environ.get("WORKSPACE_CALLBACK_SECRET")
    if not registration_url or not bootstrap:
        raise SyncError("Workspace Repository registration is not configured")
    request = Request(
        registration_url,
        headers={"X-Workspace-Bootstrap": bootstrap},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - configured Platform URL.
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            raise SyncError(
                "No active workspace is ready for Repository synchronization"
            ) from error
        raise
    return payload["repository_url"].rstrip("/"), payload["callback_token"]


def main() -> int:
    watch = os.environ.get("WORKSPACE_REPOSITORY_SYNC_WATCH", "false").lower() == "true"
    while True:
        try:
            base_url, token = _registration()
            synchronize(base_url, token)
            if not watch:
                return 0
        except (SyncError, HTTPError, URLError, OSError, ValueError) as error:
            print(f"workspace repository sync: {error}", file=sys.stderr, flush=True)
            if not watch:
                return 1
        time.sleep(max(2, INTERVAL))


if __name__ == "__main__":
    raise SystemExit(main())
