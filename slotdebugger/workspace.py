"""Per-game workspace: a `slotdebugger/` directory that holds everything the debugger reads and writes.

    slotdebugger/
      workspace.json   game name, schema version
      reports/         imported RTP reports (CSV/XLSX)
      analysis/        analyze output, one JSON per report
      data/runs.json   index of analysis runs (actual RTP per report)
      state/           thinking-CLI state (.think_state.json) and exports
"""

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Optional

DIR_NAME = "slotdebugger"
MARKER = "workspace.json"
SUBDIRS = ("reports", "analysis", "data", "state")
SCHEMA_VERSION = 2


class WorkspaceError(ValueError):
    pass


class Workspace:
    def __init__(self, root: str):
        self.root = os.path.realpath(root)

    # ---- layout ----
    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    @property
    def reports(self) -> str:
        return self.path("reports")

    @property
    def analysis(self) -> str:
        return self.path("analysis")

    @property
    def runs_file(self) -> str:
        return self.path("data", "runs.json")

    @property
    def think_state(self) -> str:
        return self.path("state", ".think_state.json")

    # ---- creation / discovery ----
    @classmethod
    def setup(cls, parent_dir: str, game: Optional[str] = None):
        """Create the workspace, or bring an existing one up to the current schema.

        Idempotent, so it is safe to re-run after upgrading slotdebugger: it adds
        directories introduced by newer versions and migrates `workspace.json` and
        `data/runs.json`. It never touches imported reports, analysis output or
        thinking state — migration only rewrites files this class owns, and the one
        legacy format it replaces is backed up rather than discarded.

        Returns `(workspace, changes)` where `changes` describes what was done.
        """
        parent = os.path.realpath(parent_dir)
        ws = cls(os.path.join(parent, DIR_NAME))
        existed = os.path.exists(ws.path(MARKER))
        changes: List[str] = []

        for sub in SUBDIRS:
            if not os.path.isdir(ws.path(sub)):
                os.makedirs(ws.path(sub), exist_ok=True)
                changes.append(f"created {sub}/")

        info: Dict = {}
        if existed:
            try:
                info = ws._read_json(ws.path(MARKER))
            except (ValueError, OSError):
                # Only regenerable metadata lives here; rebuild rather than block.
                changes.append(f"rebuilt unreadable {MARKER}")
            if not isinstance(info, dict):
                info = {}

        changes += ws._migrate_runs()

        was = info.get("version")
        updated = {
            **info,
            "game": game or info.get("game") or os.path.basename(parent),
            "version": SCHEMA_VERSION,
            "created": info.get("created") or _now(),
        }
        if existed:
            updated["updated"] = _now()
            if was != SCHEMA_VERSION:
                changes.append(f"schema {was or 'unversioned'} -> {SCHEMA_VERSION}")
            if game and game != info.get("game"):
                changes.append(f"renamed game -> {game}")
        ws._write_json(ws.path(MARKER), updated)
        return ws, changes

    def _migrate_runs(self) -> List[str]:
        """Ensure `data/runs.json` holds a list of runs, preserving anything else."""
        if not os.path.exists(self.runs_file):
            self._write_json(self.runs_file, [])
            return ["created data/runs.json"]
        try:
            data = self._read_json(self.runs_file)
        except (ValueError, OSError):
            data = None
        if isinstance(data, list):
            return []
        backup = self.path("data", "runs.legacy.json")
        if data is not None and not os.path.exists(backup):
            self._write_json(backup, data)
            self._write_json(self.runs_file, [])
            return ["moved legacy data/runs.json -> data/runs.legacy.json"]
        self._write_json(self.runs_file, [])
        return ["reset unreadable data/runs.json"]

    def record_registered(self, skills: List[str], rules: List[str]) -> None:
        """Remember what was registered, so a later `setup` can retire what we drop."""
        info = self._read_json(self.path(MARKER))
        info["registered"] = {"skills": sorted(skills), "rules": sorted(rules)}
        self._write_json(self.path(MARKER), info)

    @classmethod
    def find(cls, start: str) -> Optional["Workspace"]:
        """Nearest `slotdebugger/workspace.json` in `start` or any parent directory."""
        cur = os.path.realpath(start)
        while True:
            if os.path.exists(os.path.join(cur, DIR_NAME, MARKER)):
                return cls(os.path.join(cur, DIR_NAME))
            parent = os.path.dirname(cur)
            if parent == cur:
                return None
            cur = parent

    def info(self) -> Dict:
        return self._read_json(self.path(MARKER))

    # ---- confinement ----
    def contains(self, path: str) -> bool:
        real = os.path.realpath(path)
        return real == self.root or real.startswith(self.root + os.sep)

    def inside(self, path: str, base: Optional[str] = None) -> str:
        """Resolve `path` (relative to `base`, default the workspace root); error if it escapes the workspace."""
        full = path if os.path.isabs(path) else os.path.join(base or self.root, path)
        if not self.contains(full):
            raise WorkspaceError(f"{path!r} is outside the workspace {self.root}")
        return os.path.realpath(full)

    # ---- operations ----
    def add_report(self, source: str) -> str:
        """Copy a report into reports/ (the only place the debugger reads from outside)."""
        if not os.path.isfile(source):
            raise WorkspaceError(f"no such file: {source}")
        dest = os.path.join(self.reports, os.path.basename(source))
        if os.path.realpath(source) != os.path.realpath(dest):
            shutil.copyfile(source, dest)
        return dest

    def resolve_report(self, name: str) -> str:
        """A report given by name or path; must live in reports/."""
        # Strip "reports/" prefix if it's already there (handle both "file.xlsx" and "reports/file.xlsx")
        if name.startswith("reports/"):
            name = name[len("reports/"):]
        
        candidate = name if os.path.isabs(name) else os.path.join(self.reports, name)
        if not os.path.isfile(candidate):
            raise WorkspaceError(
                f"report {name!r} not found in {self.reports}; import it with `slotdebug add <file>`")
        return self.inside(candidate)

    def save_analysis(self, report_path: str, result: Dict, output: Optional[Dict] = None) -> str:
        """Write the analysis and index the run.

        `output` is what gets written — the game's `expected_rtp_report.json` shape when
        it has one. The run index is always taken from the native `result`, so `runs`
        stays comparable across games whatever shape their reports are written in.
        """
        stem = os.path.splitext(os.path.basename(report_path))[0]
        out = self.path("analysis", f"{stem}.json")
        self._write_json(out, result if output is None else output)
        runs = self.runs()
        runs.append({
            "report": os.path.basename(report_path),
            "analysis": os.path.relpath(out, self.root),
            "time": _now(),
            "status": result["analysis"]["status"],
            "total_rtp": result["analysis"]["total_rtp"],
            "issues": result["analysis"]["issues"],
        })
        self._write_json(self.runs_file, runs)
        return out

    def runs(self) -> List[Dict]:
        """Load runs index; handle legacy format (dict → empty list)."""
        if not os.path.exists(self.runs_file):
            return []
        data = self._read_json(self.runs_file)
        # Handle legacy format: if data is a dict, treat it as old metadata
        # and return empty list (analysis runs will be tracked separately)
        if isinstance(data, dict):
            return []
        return data if isinstance(data, list) else []

    # ---- json helpers (atomic) ----
    @staticmethod
    def _read_json(path: str):
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: str, data) -> None:
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
