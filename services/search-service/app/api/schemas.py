import enum
import uuid
from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


class SearchSortField(enum.Enum):
    RELEVANCE = "relevance"
    START_TIME = "start_time"


class SortOrder(enum.Enum):
    ASC = "asc"
    DESC = "desc"


class SearchResultItem(BaseModel):
    event_id: uuid.UUID
    title: str
    description: str | None
    start_time: datetime
    end_time: datetime
    venue_name: str
    performer_names: list[str]


class SearchResponse(BaseModel):
    items: list[SearchResultItem]
    total: int
    limit: int
    offset: int
