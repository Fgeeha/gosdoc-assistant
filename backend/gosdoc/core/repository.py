"""Доступ к доменным таблицам с обязательной фильтрацией по организации (ADR-0006)."""

from typing import Any, Generic, Protocol, TypeVar

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import InstrumentedAttribute, Session


class _OrgScoped(Protocol):
    id: Any
    organization_id: Any


M = TypeVar("M", bound=_OrgScoped)


class OrgRepository(Generic[M]):
    """Каждый запрос через репозиторий видит только строки своей организации."""

    def __init__(self, model: type[M], db: Session, organization_id: int) -> None:
        self.model = model
        self.db = db
        self.organization_id = organization_id
        self._org_col: InstrumentedAttribute[int] = model.organization_id

    def _where(self, *criteria: ColumnElement[bool]) -> Any:
        return select(self.model).where(self._org_col == self.organization_id, *criteria)

    def get(self, obj_id: int) -> M | None:
        return self.db.scalar(self._where(self.model.id == obj_id))

    def list(
        self,
        *criteria: ColumnElement[bool],
        order_by: Any = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[M]:
        stmt = self._where(*criteria).order_by(order_by if order_by is not None else self.model.id)
        return list(self.db.scalars(stmt.limit(limit).offset(offset)))

    def add(self, obj: M) -> M:
        obj.organization_id = self.organization_id
        self.db.add(obj)
        self.db.flush()
        return obj
