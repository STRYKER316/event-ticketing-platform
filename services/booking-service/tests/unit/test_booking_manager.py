import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from shared_auth import Principal

from app.db.models import Booking, BookingStatus, Ticket, TicketStatus
from app.logic.booking_manager import BookingManager
from app.logic.helpers.fake_hold_strategy import FakeHoldStrategy

pytestmark = pytest.mark.asyncio

USER = Principal(subject="user-1", roles=[])


def _stamp_generated_fields(booking: Booking) -> Booking:
    # Simulates what BaseRepository.create()'s real flush() does: populates
    # the client-side `id` default and (via implicit RETURNING) the
    # server-side `created_at` default — neither of which a bare AsyncMock
    # would ever set.
    booking.id = uuid.uuid4()
    booking.created_at = datetime.now(timezone.utc)
    return booking


async def test_create_booking_happy_path():
    ticket_id = uuid.uuid4()
    event_id = uuid.uuid4()
    ticket = Ticket(id=ticket_id, event_id=event_id, section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.AVAILABLE)

    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=ticket)),
        bookings=AsyncMock(create=AsyncMock(side_effect=_stamp_generated_fields)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    result = await manager.create_booking(USER, ticket_id)

    assert result.status is BookingStatus.PENDING
    assert result.ticket_id == ticket_id
    assert result.user_subject == USER.subject
    assert result.event_id == event_id


async def test_list_tickets_for_event_maps_ticket_id_from_model_id():
    # The DTO field is named ticket_id (clearer for a frontend joining it
    # against a seat map) but the model's own field is id — this is the one
    # manual mapping step from_attributes can't do for us, so it's the one
    # thing this test exists to pin down.
    event_id = uuid.uuid4()
    ticket = Ticket(
        id=uuid.uuid4(), event_id=event_id, section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.AVAILABLE
    )
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(list_by_event=AsyncMock(return_value=[ticket])),
        bookings=AsyncMock(),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    result = await manager.list_tickets_for_event(event_id)

    assert len(result) == 1
    assert result[0].ticket_id == ticket.id
    assert result[0].section == "A"
    assert result[0].row_name == "1"
    assert result[0].seat_label == "A1"
    assert result[0].status is TicketStatus.AVAILABLE
    assert result[0].price_cents == 2500


async def test_list_tickets_for_event_empty_for_no_tickets():
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(list_by_event=AsyncMock(return_value=[])),
        bookings=AsyncMock(),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    result = await manager.list_tickets_for_event(uuid.uuid4())

    assert result == []


async def test_create_booking_on_unknown_ticket_404s():
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=None)),
        bookings=AsyncMock(),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.create_booking(USER, uuid.uuid4())
    assert exc_info.value.status_code == 404


async def test_create_booking_on_already_booked_ticket_409s():
    ticket_id = uuid.uuid4()
    ticket = Ticket(id=ticket_id, event_id=uuid.uuid4(), section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.BOOKED)
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=ticket)),
        bookings=AsyncMock(),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.create_booking(USER, ticket_id)
    assert exc_info.value.status_code == 409


async def test_create_booking_when_hold_already_taken_409s():
    ticket_id = uuid.uuid4()
    ticket = Ticket(id=ticket_id, event_id=uuid.uuid4(), section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.AVAILABLE)
    strategy = FakeHoldStrategy()
    await strategy.acquire_hold(ticket_id, ttl_seconds=60)  # pre-held by someone else

    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=ticket)),
        bookings=AsyncMock(),
        hold_strategy=strategy,
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.create_booking(USER, ticket_id)
    assert exc_info.value.status_code == 409


def _pending_booking(booking_id: uuid.UUID, ticket_id: uuid.UUID, user_subject: str = USER.subject) -> Booking:
    return Booking(
        id=booking_id,
        user_subject=user_subject,
        event_id=uuid.uuid4(),
        ticket_id=ticket_id,
        status=BookingStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )


def _fake_http_client(json_body: dict) -> AsyncMock:
    response = MagicMock(raise_for_status=MagicMock(), json=MagicMock(return_value=json_body))
    return AsyncMock(post=AsyncMock(return_value=response))


async def test_pay_booking_happy_path_calls_payment_service_with_ticket_price():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _pending_booking(booking_id, ticket_id)
    ticket = Ticket(id=ticket_id, event_id=booking.event_id, section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.HELD)
    http_client = _fake_http_client(
        {"id": str(uuid.uuid4()), "status": "pending", "amount_cents": 2500, "currency": "usd"}
    )
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=ticket)),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=booking)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    result = await manager.pay_booking(USER, booking_id, "token-abc", http_client)

    assert result.amount_cents == 2500
    call = http_client.post.await_args
    assert call.args[0].endswith("/payments/charge")
    assert call.kwargs["json"]["amount_cents"] == 2500
    assert call.kwargs["headers"]["Authorization"] == "Bearer token-abc"


async def test_pay_booking_on_unknown_booking_404s():
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=None)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.pay_booking(USER, uuid.uuid4(), "token", AsyncMock())
    assert exc_info.value.status_code == 404


async def test_pay_booking_by_non_owner_403s():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _pending_booking(booking_id, ticket_id, user_subject="someone-else")
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=booking)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.pay_booking(USER, booking_id, "token", AsyncMock())
    assert exc_info.value.status_code == 403


async def test_pay_booking_on_non_pending_booking_409s():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _pending_booking(booking_id, ticket_id)
    booking.status = BookingStatus.CONFIRMED
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=booking)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.pay_booking(USER, booking_id, "token", AsyncMock())
    assert exc_info.value.status_code == 409


async def test_pay_booking_502s_when_payment_service_unreachable():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _pending_booking(booking_id, ticket_id)
    ticket = Ticket(id=ticket_id, event_id=booking.event_id, section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.HELD)
    http_client = AsyncMock(post=AsyncMock(side_effect=httpx.ConnectError("boom")))
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=ticket)),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=booking)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.pay_booking(USER, booking_id, "token", http_client)
    assert exc_info.value.status_code == 502


async def test_pay_booking_forwards_payment_service_status_when_it_answers_with_an_error():
    # Payment Service being reachable and rejecting the request (e.g. its
    # own 502 when Stripe is down) is a different failure than a connection
    # error — must not collapse into the same misleading "unreachable" 502
    # regardless of what Payment Service actually said (found in code review).
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _pending_booking(booking_id, ticket_id)
    ticket = Ticket(id=ticket_id, event_id=booking.event_id, section="A", row_name="1", seat_label="A1", price_cents=2500, status=TicketStatus.HELD)
    error_response = httpx.Response(422, request=httpx.Request("POST", "http://payment-service/payments/charge"))
    http_client = AsyncMock(
        post=AsyncMock(
            return_value=MagicMock(raise_for_status=MagicMock(side_effect=httpx.HTTPStatusError("bad", request=error_response.request, response=error_response)))
        )
    )
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(get_by_id=AsyncMock(return_value=ticket)),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=booking)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.pay_booking(USER, booking_id, "token", http_client)
    assert exc_info.value.status_code == 422


def _confirmed_booking(booking_id: uuid.UUID, ticket_id: uuid.UUID, user_subject: str = USER.subject) -> Booking:
    return Booking(
        id=booking_id,
        user_subject=user_subject,
        event_id=uuid.uuid4(),
        ticket_id=ticket_id,
        status=BookingStatus.CONFIRMED,
        created_at=datetime.now(timezone.utc),
    )


def _future_start_time() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=2)


async def test_cancel_booking_happy_path_releases_seat_and_cancels():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _confirmed_booking(booking_id, ticket_id)
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(
            get_by_id=AsyncMock(return_value=booking),
            transition_if_confirmed=AsyncMock(return_value=True),
        ),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(get_start_time=AsyncMock(return_value=_future_start_time())),
    )
    cancelled_producer = AsyncMock()

    result = await manager.cancel_booking(USER, booking_id, cancelled_producer)

    assert result.status is BookingStatus.CANCELLED
    cancelled_producer.publish_cancelled.assert_awaited_once_with(booking_id)


async def test_cancel_booking_with_no_event_start_time_on_record_409s():
    # Fail closed, not open (found in code review): a missing Event row
    # (only reachable for a booking whose event predates this table) must
    # not silently skip the cancellation-cutoff check §22 amendment #2
    # exists to enforce.
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _confirmed_booking(booking_id, ticket_id)
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=booking)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(get_start_time=AsyncMock(return_value=None)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.cancel_booking(USER, booking_id, AsyncMock())
    assert exc_info.value.status_code == 409


async def test_cancel_booking_on_unknown_booking_404s():
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=None)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.cancel_booking(USER, uuid.uuid4(), AsyncMock())
    assert exc_info.value.status_code == 404


async def test_cancel_booking_by_non_owner_403s():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _confirmed_booking(booking_id, ticket_id, user_subject="someone-else")
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=booking)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.cancel_booking(USER, booking_id, AsyncMock())
    assert exc_info.value.status_code == 403


async def test_cancel_booking_on_non_confirmed_booking_409s():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _pending_booking(booking_id, ticket_id)
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=booking)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.cancel_booking(USER, booking_id, AsyncMock())
    assert exc_info.value.status_code == 409


async def test_cancel_booking_past_event_start_409s():
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _confirmed_booking(booking_id, ticket_id)
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(get_by_id=AsyncMock(return_value=booking)),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(get_start_time=AsyncMock(return_value=datetime.now(timezone.utc) - timedelta(hours=1))),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.cancel_booking(USER, booking_id, AsyncMock())
    assert exc_info.value.status_code == 409


async def test_cancel_booking_race_lost_409s():
    # transition_if_confirmed matching zero rows — lost a race to a
    # concurrent cancel or expiry sweep (same reasoning as
    # _create_booking_row's own integrity-race handling).
    booking_id, ticket_id = uuid.uuid4(), uuid.uuid4()
    booking = _confirmed_booking(booking_id, ticket_id)
    manager = BookingManager(
        session=AsyncMock(),
        tickets=AsyncMock(),
        bookings=AsyncMock(
            get_by_id=AsyncMock(return_value=booking),
            transition_if_confirmed=AsyncMock(return_value=False),
        ),
        hold_strategy=FakeHoldStrategy(),
        events=AsyncMock(get_start_time=AsyncMock(return_value=_future_start_time())),
    )

    with pytest.raises(HTTPException) as exc_info:
        await manager.cancel_booking(USER, booking_id, AsyncMock())
    assert exc_info.value.status_code == 409
