import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from urllib.error import HTTPError

import pytest

from app.services.position_ingestor import PositionIngestor
from app.vesselapi import VesselAPIService, normalize_vesselapi_position


MMSI = "311001056"
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def response(**fields: Any) -> dict[str, Any]:
    """Build a minimal provider response with specified field overrides."""
    return {
        "vesselPosition": {
            "mmsi": int(MMSI),
            "latitude": 37.5,
            "longitude": 23.5,
            "timestamp": "2026-10-09T11:45:00Z",
            **fields,
        }
    }


def service(
    last_ais: datetime | None = None,
    key: str = "test-key",
    mmsis: list[str] | None = None,
) -> VesselAPIService:
    """Use the real ingestor with mocked persistence and provider state."""
    database = SimpleNamespace(active_mmsis=AsyncMock(return_value=mmsis or [MMSI]))
    ingestor = PositionIngestor(database, Mock(), Mock(), sample_seconds=60)
    ais = SimpleNamespace(last_position_at={} if last_ais is None else {MMSI: last_ais})
    return VesselAPIService(
        SimpleNamespace(vesselapi_api_key=key), database, ingestor, ais
    )


def test_normalization_preserves_observation_time_and_optional_nulls() -> None:
    """Preserve provider time and normalize missing or supplied optional fields."""
    position = normalize_vesselapi_position(response(), MMSI, NOW)
    assert position is not None
    assert position.received_at == NOW - timedelta(minutes=15)
    assert (position.latitude, position.longitude) == (37.5, 23.5)
    assert (position.data_provider, position.data_source) == (
        "vesselapi",
        "terrestrial",
    )
    assert position.speed_over_ground_knots is None
    assert position.course_over_ground_degrees is None
    assert position.true_heading_degrees is None
    assert position.navigational_status is None
    position = normalize_vesselapi_position(
        response(
            timestamp="2026-10-09T13:45:00+02:00",
            sog=12.5,
            cog=180,
            heading=181,
            nav_status=0,
            vessel_name="Resilient Lady",
        ),
        MMSI,
        NOW,
    )
    assert position.received_at == NOW - timedelta(minutes=15)
    assert position.speed_over_ground_knots == 12.5
    assert position.true_heading_degrees == 181
    assert position.navigational_status == 0
    assert position.display_name == "Resilient Lady"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"vesselPosition": None},
        response(latitude=None),
        response(latitude=91),
        response(longitude=-181),
        response(latitude="invalid"),
        response(latitude=float("nan")),
        response(longitude=float("inf")),
        response(mmsi="123456789"),
        response(mmsi=None),
        response(timestamp=None),
        response(timestamp="invalid"),
        response(timestamp="2026-10-09T11:45:00"),
        response(timestamp="2026-10-09T12:06:00Z"),
        response(suspected_glitch=True),
    ],
)
def test_invalid_provider_positions_are_rejected(payload: Any) -> None:
    """Reject malformed, mismatched, future, or flagged positions."""
    assert normalize_vesselapi_position(payload, MMSI, NOW) is None


def test_fallback_freshness_hourly_limit_and_stale_ingestion() -> None:
    """Request stale AIS hourly while the shared ingestor rejects old positions."""

    async def check() -> None:
        """Exercise freshness and ingestion in one event loop."""
        fallback = service(last_ais=NOW - timedelta(minutes=59))
        with patch.object(fallback, "_fetch", return_value=response()) as fetch:
            await fallback.poll(NOW)
            fetch.assert_not_called()
            await fallback.poll(NOW + timedelta(minutes=1))
            assert fetch.call_count == 1
            assert fallback.status.accepted_positions == 1
            await fallback.poll(NOW + timedelta(hours=1))
            assert fetch.call_count == 1
            await fallback.poll(NOW + timedelta(hours=1, minutes=1))
            assert fetch.call_count == 2
            assert fallback.status.rejected_positions == 1
            fetch.return_value = response(timestamp="2026-10-09T11:44:00Z")
            await fallback.poll(NOW + timedelta(hours=2, minutes=1))
            assert fallback.status.rejected_positions == 2
            assert fallback.ingestor.metrics.stale_positions == 2
            assert fallback.ingestor.writer.submit.call_count == 1
            assert fallback.ingestor.broadcaster.publish.call_count == 1

    asyncio.run(check())


def test_never_seen_vessel_and_errors_are_limited_to_hourly_requests() -> None:
    """Query an unseen vessel immediately without retrying errors before an hour."""

    async def check() -> None:
        """Exercise the request budget despite failures."""
        fallback = service()
        with patch.object(fallback, "_fetch", side_effect=TimeoutError) as fetch:
            await fallback.poll(NOW)
            await fallback.poll(NOW + timedelta(minutes=59))
            assert fetch.call_count == 1
            assert fallback.status.errors == 1
            await fallback.poll(NOW + timedelta(hours=1))
            assert fetch.call_count == 2
            assert fallback.status.errors == 2

    asyncio.run(check())


def test_rate_limit_blocks_other_vessels_until_retry_after() -> None:
    """Respect provider rate limits across every tracked vessel."""

    async def check() -> None:
        """Exercise shared rate limiting through the polling entry point."""
        fallback = service(mmsis=[MMSI, "123456789"])
        error = HTTPError(
            "https://api.vesselapi.com",
            429,
            "rate limit",
            {"Retry-After": "7200"},
            None,
        )
        with patch.object(fallback, "_fetch", side_effect=error) as fetch:
            await fallback.poll(NOW)
            assert fetch.call_count == 1
            await fallback.poll(NOW + timedelta(hours=1))
            assert fetch.call_count == 1
            await fallback.poll(NOW + timedelta(hours=2))
            assert fetch.call_count == 2

    asyncio.run(check())


def test_missing_key_and_shutdown_make_no_requests() -> None:
    """Disable ingestion without a key and stop without further provider calls."""

    async def check() -> None:
        """Exercise disabled and stopped service lifecycle states."""
        missing_key = service(key="")
        with patch.object(missing_key, "_fetch") as fetch:
            await missing_key.run()
            fetch.assert_not_called()
            assert missing_key.status.state == "disabled_missing_api_key"
        stopped = service()
        stopped.stop()
        with patch.object(stopped, "_fetch") as fetch:
            await stopped.run()
            await stopped.poll(NOW)
            fetch.assert_not_called()

    asyncio.run(check())


def test_http_request_uses_bearer_auth_and_terrestrial_endpoint() -> None:
    """Use authenticated terrestrial requests with a bounded network timeout."""
    fallback = service()
    http_response = Mock()
    http_response.read.return_value = json.dumps(response()).encode()
    with patch("app.vesselapi.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = http_response
        assert fallback._fetch(MMSI) == response()
    request = urlopen.call_args.args[0]
    assert request.full_url == (
        f"https://api.vesselapi.com/v1/vessel/{MMSI}/position?filter.idType=mmsi"
    )
    assert request.get_header("Authorization") == "Bearer test-key"
    assert urlopen.call_args.kwargs["timeout"] == 20
