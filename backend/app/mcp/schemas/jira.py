from pydantic import BaseModel, Field


class GetProjectInput(BaseModel):
    project_id: str = Field(min_length=1)


class ProjectData(BaseModel):
    project_id: str
    name: str
    key: str
    description: str | None = None


class GetProjectOutput(BaseModel):
    project: ProjectData