"""Projects: the owner's own projects, each run through a Coordinator. See docs/0_13_projects.md, section 7.2.

The core keeps projects and their sessions; this plugin makes and removes its own, and its UI is the Projects feature.
"""

# No `from __future__ import annotations`: FastAPI reads the request body's model,
# defined inside register(), from the route's annotations.


def register(api) -> None:
    from fastapi import HTTPException
    from pydantic import BaseModel

    from agentop import ops, projects

    from .texts import INSTRUCTIONS, WELCOME

    class NewProject(BaseModel):
        name: str

    def own(name: str) -> dict:
        found = projects.get(name)
        if found is None or found.get("owner") != api.id:
            raise HTTPException(404, "No such project")
        return found

    @api.router.get("/projects")
    async def list_projects():
        return projects.list_projects(api.id)

    @api.router.post("/projects", status_code=201)
    async def create_project(body: NewProject):
        try:
            return await api.create_project(body.name.strip(), WELCOME, INSTRUCTIONS)
        except projects.ProjectError as exc:
            raise HTTPException(400, str(exc)) from None

    @api.router.post("/projects/{name}/coordinator", status_code=202)
    async def start_coordinator(name: str):
        own(name)
        row = await api.start_coordinator(name)
        if row is None:
            raise HTTPException(409, "No agent tool is installed")
        return row

    @api.router.delete("/projects/{name}")
    async def remove_project(name: str, delete_folder: bool = False):
        own(name)
        try:
            await api.remove_project(name, delete_folder)
        except projects.ProjectError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True}

    def stop_all() -> None:
        mine = {p["name"] for p in projects.list_projects(api.id)}
        for row in api.sessions():
            if row.get("project") in mine and row.get("managed") and row["status"] == "running":
                try:
                    ops.stop(row["id"])
                except ops.OpsError as exc:
                    api.log.warning("Turning off: %s", exc)

    api.on_disable(stop_all)
