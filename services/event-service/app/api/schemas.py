from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class DemoResponse(BaseModel):
    message: str
    username: str | None
    roles: list[str]
