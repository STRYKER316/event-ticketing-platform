import uuid

import pytest
from pydantic import ValidationError

from app.api.schemas import BookingCreate


def test_ticket_id_must_be_a_valid_uuid():
    with pytest.raises(ValidationError):
        BookingCreate(ticket_id="not-a-uuid")


def test_valid_ticket_id_accepted():
    ticket_id = uuid.uuid4()
    assert BookingCreate(ticket_id=ticket_id).ticket_id == ticket_id
