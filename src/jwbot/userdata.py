"""Small user-editable data files (price targets) + best-effort git sync.

Targets live in `data/targets.json` (git-tracked) so the weekly cloud run and
the local bot both see them. When the local bot changes targets or custom
bottles it *tries* to commit and push; if the machine has no push access the
change still works locally and the bot says so honestly.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def bot_version() -> str:
    try:
        return (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def git_short_sha() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (proc.stdout.strip() or None) if proc.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Price targets
# --------------------------------------------------------------------------- #
def load_targets(path: Path) -> dict[str, float]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Could not read targets file %s: %s", path, exc)
        return {}
    out: dict[str, float] = {}
    for key, value in (raw.get("targets", {}) if isinstance(raw, dict) else {}).items():
        try:
            out[str(key)] = float(value)
        except (TypeError, ValueError):
            log.warning("Ignoring non-numeric target for %r: %r", key, value)
    return out


def save_targets(path: Path, targets: dict[str, float]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"targets": {k: round(float(v), 2) for k, v in sorted(targets.items())}}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def target_hits(results, targets: dict[str, float]) -> list[tuple[object, float]]:
    """[(PriceResult, target)] for every priced listing at or under its target."""
    hits = []
    for result in results:
        if not getattr(result, "ok", False) or result.product_key not in targets:
            continue
        target = targets[result.product_key]
        if result.price is not None and result.price <= target + 0.005:
            hits.append((result, target))
    return hits


# --------------------------------------------------------------------------- #
# Git sync (best effort - never raises)
# --------------------------------------------------------------------------- #
def git_sync(paths: list[Path], message: str, repo_root: Path | None = None) -> tuple[bool, str]:
    """git add+commit+push the given files. Returns (pushed, detail)."""
    root = Path(repo_root or PROJECT_ROOT)

    def run(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=timeout
        )

    try:
        if run("rev-parse", "--is-inside-work-tree").returncode != 0:
            return False, "not a git checkout"
        rels = [str(Path(p).resolve().relative_to(root.resolve())) for p in paths]
        run("add", "--", *rels)
        status = run("status", "--porcelain", "--", *rels)
        if not status.stdout.strip():
            return True, "nothing to commit"
        commit = run("commit", "-m", f"{message} [skip notes]")
        if commit.returncode != 0:
            return False, (commit.stderr or commit.stdout).strip()[-200:] or "commit failed"
        run("pull", "--rebase", "--autostash", timeout=120)
        push = run("push", timeout=120)
        if push.returncode != 0:
            return False, (push.stderr or push.stdout).strip()[-200:] or "push failed"
        return True, "pushed"
    except subprocess.TimeoutExpired:
        return False, "git timed out"
    except Exception as exc:  # noqa: BLE001 - sync must never crash the bot
        return False, f"{type(exc).__name__}: {exc}"
