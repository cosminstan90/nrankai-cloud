from fastapi import APIRouter
from workers.site_design_detector import detect

router = APIRouter()


@router.post("/site-design")
async def site_design_check(payload: dict):
    url = payload.get("url")
    result = await detect(url)
    return result
