"""
Scheduler health and inspection endpoints. All times in IST.
"""

from fastapi import APIRouter, Depends, Header, HTTPException

from backend.auth.jwt_handler import get_user_id_from_token
from backend.utils.time_utils import format_ist, now_ist

router = APIRouter(prefix="/scheduler", tags=["Scheduler"])


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    user_id = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


@router.get("/status")
def scheduler_status(user_id: int = Depends(get_user_id)):
    """
    Shows scheduler health and upcoming jobs — all times in IST.
    """
    from backend import scheduler as scheduler_module

    sched = scheduler_module.scheduler

    if not sched or not sched.running:
        return {
            "running": False,
            "current_time_ist": format_ist(now_ist()),
            "message": "Scheduler is not running",
        }

    jobs = []
    for job in sched.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": format_ist(next_run) if next_run else "not scheduled",
        })

    return {
        "running": True,
        "current_time_ist": format_ist(now_ist()),
        "timezone": "IST (Asia/Kolkata, UTC+5:30)",
        "jobs": jobs,
    }
