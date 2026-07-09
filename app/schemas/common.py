from pydantic import BaseModel


class Message(BaseModel):
    message: str


class TaskAccepted(BaseModel):
    task_id: str | None = None
    status: str
    resource_id: int
