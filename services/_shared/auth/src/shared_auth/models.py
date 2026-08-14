from pydantic import BaseModel


class Principal(BaseModel):
    subject: str
    username: str | None = None
    email: str | None = None
    roles: list[str] = []

    def has_role(self, role: str) -> bool:
        return role in self.roles
