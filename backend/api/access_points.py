from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from schemas.access_points import AccessPointDeviceDetail, AccessPointDeviceList, AccessPointStatus
from services.access_points import get_device, list_devices


router = APIRouter(prefix="/access-points", tags=["access-points"])


@router.get("/devices", response_model=AccessPointDeviceList)
async def list_access_point_devices(
    status: AccessPointStatus | None = Query(default=None),
    keyword: str | None = Query(default=None),
) -> AccessPointDeviceList:
    """List devices automatically derived from gateway telemetry records."""
    return await list_devices(status=status, keyword=keyword)


@router.get("/devices/{device_id}", response_model=AccessPointDeviceDetail)
async def get_access_point_device(device_id: str) -> AccessPointDeviceDetail:
    """Return one device and all of its latest sensor points."""
    detail = await get_device(device_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Device not found.")
    return detail
