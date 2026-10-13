"""Hourly terrestrial VesselAPI fallback for vessels missing AISStream updates."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.aisstream.client import AisStreamService
from app.config import Settings
from app.domain.models import PositionObservation
from app.domain.normalize import normalize_position
from app.persistence.database import Database
from app.services.position_ingestor import PositionIngestor

logger = logging.getLogger(__name__)
INTERVAL_SECONDS = 3600


@dataclass
class VesselAPIStatus:
    """Fallback request and ingestion counters exposed by the status endpoint."""

    state: str = "starting"
    requests: int = 0
    accepted_positions: int = 0
    rejected_positions: int = 0
    errors: int = 0
    last_request_at: str | None = None
    last_error: str | None = None

    def public_dict(self) -> dict[str, Any]:
        """Return JSON-compatible status fields."""
        return asdict(self)


class VesselAPIService:
    """Poll stale tracked vessels at most hourly without satellite credit use."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        ingestor: PositionIngestor,
        ais: AisStreamService,
    ) -> None:
        """Reuse the existing tracked vessels and common ingestion pipeline."""
        self.settings = settings
        self.database = database
        self.ingestor = ingestor
        self.ais = ais
        self.status = VesselAPIStatus()
        self._stop = asyncio.Event()
        self._next_request: dict[str, datetime] = {}
        self._blocked_until: datetime | None = None

    def stop(self) -> None:
        """Interrupt the polling wait on application shutdown."""
        self._stop.set()

    async def run(self) -> None:
        """Check freshness each minute and request stale vessels hourly."""
        if not self.settings.vesselapi_api_key:
            self.status.state = "disabled_missing_api_key"
            return
        self.status.state = "waiting"
        while not self._stop.is_set():
            try:
                await self.poll(datetime.now(timezone.utc))
            except Exception as exc:
                self._record_error(type(exc).__name__)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def poll(self, now: datetime) -> None:
        """Query active vessels with no AISStream position in the past hour."""
        if self._blocked_until is not None and now < self._blocked_until:
            return
        for mmsi in await self.database.active_mmsis():
            if self._stop.is_set():
                return
            last_ais = self.ais.last_position_at.get(mmsi)
            if last_ais is not None and now - last_ais < timedelta(hours=1):
                continue
            if now < self._next_request.get(mmsi, now):
                continue
            self._next_request[mmsi] = now + timedelta(seconds=INTERVAL_SECONDS)
            self.status.requests += 1
            self.status.last_request_at = now.isoformat().replace("+00:00", "Z")
            try:
                payload = await asyncio.to_thread(self._fetch, mmsi)
                position = normalize_vesselapi_position(payload, mmsi, now)
                if position is None:
                    self.status.rejected_positions += 1
                else:
                    result = await self.ingestor.submit(position)
                    if result.accepted:
                        self.status.accepted_positions += 1
                    else:
                        self.status.rejected_positions += 1
                self.status.state = "waiting"
                self.status.last_error = None
            except HTTPError as exc:
                self._record_error(f"HTTP {exc.code}")
                if exc.code in {401, 403, 429}:
                    retry_after = exc.headers.get("Retry-After", "0")
                    seconds = int(retry_after) if retry_after.isdigit() else 0
                    self._blocked_until = now + timedelta(
                        seconds=max(INTERVAL_SECONDS, seconds)
                    )
                    return
            except Exception as exc:
                self._record_error(type(exc).__name__)

    def _fetch(self, mmsi: str) -> dict[str, Any]:
        """Request the latest terrestrial position with a bounded timeout."""
        request = Request(
            f"https://api.vesselapi.com/v1/vessel/{mmsi}/position?filter.idType=mmsi",
            headers={
                "Authorization": f"Bearer {self.settings.vesselapi_api_key}",
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read(1_048_576))

    def _record_error(self, message: str) -> None:
        """Report failures without logging credentials or provider response bodies."""
        self.status.state = "error"
        self.status.errors += 1
        self.status.last_error = message
        logger.warning("VesselAPI fallback failed: %s", message)


def normalize_vesselapi_position(
    response: dict[str, Any], mmsi: str, now: datetime
) -> PositionObservation | None:
    """Validate a provider position and preserve its original observation time."""
    if not isinstance(response, dict):
        return None
    payload = response.get("vesselPosition")
    if not isinstance(payload, dict) or payload.get("suspected_glitch") is True:
        return None
    if str(payload.get("mmsi")) != mmsi:
        return None
    try:
        observed_at = datetime.fromisoformat(
            payload["timestamp"].replace("Z", "+00:00")
        )
    except (KeyError, TypeError, AttributeError, ValueError):
        return None
    if observed_at.tzinfo is None or observed_at > now + timedelta(minutes=5):
        return None
    position = normalize_position(
        {
            "MessageType": "PositionReport",
            "MetaData": {"MMSI": mmsi, "ShipName": payload.get("vessel_name")},
            "Message": {
                "PositionReport": {
                    "Latitude": payload.get("latitude"),
                    "Longitude": payload.get("longitude"),
                    "Sog": payload.get("sog"),
                    "Cog": payload.get("cog"),
                    "TrueHeading": payload.get("heading"),
                    "NavigationalStatus": payload.get("nav_status"),
                }
            },
        },
        observed_at.astimezone(timezone.utc),
    )
    if position is not None:
        position.data_provider = "vesselapi"
        position.data_source = "terrestrial"
    return position
