from app.models.control_model import ControlProject


class InMemoryProjectStore:
    def __init__(self) -> None:
        self._projects: dict[str, ControlProject] = {}

    def save(self, project: ControlProject) -> ControlProject:
        if not project.file_hash:
            raise ValueError("Project must have a file hash before it can be stored.")

        self._projects[project.file_hash] = project
        return project

    def get(self, project_id: str) -> ControlProject:
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise KeyError(f"Project {project_id} was not found.") from exc

    def list(self) -> list[ControlProject]:
        return list(self._projects.values())


project_store = InMemoryProjectStore()
