from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class Seat(BaseModel):
    label: str = Field(min_length=1)
    x: float
    y: float


class SeatMapRow(BaseModel):
    name: str = Field(min_length=1)
    seats: list[Seat]


class SeatMapSection(BaseModel):
    name: str = Field(min_length=1)
    rows: list[SeatMapRow]


class SeatMap(BaseModel):
    event_id: str
    sections: list[SeatMapSection]
