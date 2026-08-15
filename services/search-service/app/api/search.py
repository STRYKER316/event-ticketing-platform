from elasticsearch import AsyncElasticsearch
from fastapi import APIRouter, Depends, Query

from app.api.schemas import SearchResponse, SearchSortField, SortOrder
from app.core import get_es_client
from app.logic.search_manager import SearchManager

router = APIRouter()


@router.get("/search", response_model=SearchResponse)
async def search_events(
    q: str = Query(default="", description="Free-text query across title/description/venue/performers"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_field: SearchSortField = Query(default=SearchSortField.RELEVANCE),
    sort_order: SortOrder = Query(default=SortOrder.DESC),
    client: AsyncElasticsearch = Depends(get_es_client),
) -> SearchResponse:
    """Public — no auth required."""
    return await SearchManager(client).search(q, limit, offset, sort_field, sort_order)
