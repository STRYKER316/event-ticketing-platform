import uuid

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.schemas import SeatMap

SEAT_MAPS_COLLECTION = "seat_maps"


class SeatMapRepository:
    def __init__(self, mongo_db: AsyncIOMotorDatabase):
        self._collection = mongo_db[SEAT_MAPS_COLLECTION]

    async def upsert(self, seat_map: SeatMap) -> None:
        await self._collection.update_one(
            {"event_id": str(seat_map.event_id)},
            {"$set": seat_map.model_dump(mode="json")},
            upsert=True,
        )

    async def get_by_event_id(self, event_id: uuid.UUID) -> SeatMap | None:
        document = await self._collection.find_one({"event_id": str(event_id)}, {"_id": 0})
        return SeatMap.model_validate(document) if document else None

    async def delete(self, event_id: uuid.UUID) -> None:
        await self._collection.delete_one({"event_id": str(event_id)})
