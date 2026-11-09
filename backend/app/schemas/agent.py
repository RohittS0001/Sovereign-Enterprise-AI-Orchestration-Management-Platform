from typing import Literal

from pydantic import BaseModel, Field


class AgentQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)

    def normalized_query(self) -> str:
        return self.query.strip()


class AgentQueryResponse(BaseModel):
    status: Literal["received", "completed", "failed"]
    query: str
    message: str
