from typing import Generic, Iterable, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: Type[ModelT]

    def __init__(self, db: Session):
        self.db = db

    def get(self, id_: int) -> ModelT | None:
        return self.db.get(self.model, id_)

    def list(self, *, limit: int = 500, offset: int = 0) -> list[ModelT]:
        stmt = select(self.model).order_by(self.model.id.desc()).limit(limit).offset(offset)
        return list(self.db.scalars(stmt))

    def create(self, obj: ModelT) -> ModelT:
        self.db.add(obj)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def add_all(self, objs: Iterable[ModelT]) -> None:
        self.db.add_all(list(objs))
        self.db.commit()

    def update(self, obj: ModelT, **fields) -> ModelT:
        for key, value in fields.items():
            setattr(obj, key, value)
        self.db.commit()
        self.db.refresh(obj)
        return obj

    def delete(self, obj: ModelT) -> None:
        self.db.delete(obj)
        self.db.commit()
