"""In-memory project storage.

This module owns the lifecycle of parsed ``ControlProject`` instances
during a development run. It also memoizes the *normalized reasoning*
output for the most recently uploaded project so the new ``/api/`` v1
endpoints can serve traces without re-running normalization on every
request.

Everything here is intentionally in-process (no database). Restarting
the backend wipes all stored projects and normalized caches.
"""

from typing import Any, Optional

from app.models.control_model import ControlProject
from app.services.normalization_service import normalize_l5x_project


class InMemoryProjectStore:
    """Process-local project store keyed by ``file_hash``.

    In addition to plain CRUD on parsed ``ControlProject`` instances,
    the store tracks:

    * ``_latest_project_id`` -- the file hash of the most recently
      saved project, so v1 endpoints can answer "trace against the
      project I just uploaded" without the caller having to thread
      the project id through every request.
    * ``_normalized_cache`` -- a per-project memo of
      ``normalize_l5x_project(project)``. Saving a project under the
      same hash invalidates its cached normalization so re-uploads
      don't serve stale graphs.
    """

    def __init__(self) -> None:
        self._projects: dict[str, ControlProject] = {}
        self._normalized_cache: dict[str, dict[str, Any]] = {}
        self._latest_project_id: Optional[str] = None

    # -- CRUD ---------------------------------------------------------

    def save(self, project: ControlProject) -> ControlProject:
        if not project.file_hash:
            raise ValueError(
                "Project must have a file hash before it can be stored."
            )

        self._projects[project.file_hash] = project
        self._latest_project_id = project.file_hash
        # A re-upload with the same hash is a no-op for the parsed
        # data, but the normalized output is cheap to recompute and we
        # don't want stale caches lingering after schema/normalizer
        # changes during dev iteration.
        self._normalized_cache.pop(project.file_hash, None)
        return project

    def get(self, project_id: str) -> ControlProject:
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise KeyError(f"Project {project_id} was not found.") from exc

    def list(self) -> list[ControlProject]:
        return list(self._projects.values())

    # -- Latest-project helpers (used by v1 endpoints) ----------------

    def latest(self) -> Optional[ControlProject]:
        """Return the most recently saved project, or None if empty."""

        if self._latest_project_id is None:
            return None
        return self._projects.get(self._latest_project_id)

    def latest_id(self) -> Optional[str]:
        return self._latest_project_id

    # -- Normalization memo -------------------------------------------

    def get_normalized(self, project_id: str) -> dict[str, Any]:
        """Return the normalized reasoning output for a project.

        Lazily computes ``normalize_l5x_project(project)`` the first
        time it is requested for a given ``project_id`` and caches the
        result. Raises ``KeyError`` if the project isn't stored.
        """

        cached = self._normalized_cache.get(project_id)
        if cached is not None:
            return cached
        project = self.get(project_id)  # may raise KeyError
        normalized = normalize_l5x_project(project)
        self._normalized_cache[project_id] = normalized
        return normalized

    def get_latest_normalized(self) -> Optional[dict[str, Any]]:
        """Convenience: normalized output for the most recent project,
        or ``None`` if no project has been uploaded yet."""

        if self._latest_project_id is None:
            return None
        return self.get_normalized(self._latest_project_id)

    # -- Test / dev helpers -------------------------------------------

    def reset(self) -> None:
        """Wipe all stored projects and caches. For tests / dev only."""

        self._projects.clear()
        self._normalized_cache.clear()
        self._latest_project_id = None


project_store = InMemoryProjectStore()
