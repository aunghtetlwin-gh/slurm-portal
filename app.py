from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from datetime import datetime
from pydantic import BaseModel, Field
from pathlib import Path
import subprocess
import requests
import time
from typing import Annotated
from fastapi import Depends

from auth import (
    router as auth_router,
    init_db,
    get_identity,
    require_csrf,
)


app = FastAPI(
    title="Slurm Portal API",
    description="FastAPI gateway for Slurm using slurmrestd",
    version="0.2.0",
)

init_db()
app.include_router(auth_router)

# =========================================================
# Configuration
# =========================================================

SLURMRESTD = "http://127.0.0.1:6820"
SLURM_USER = "lailai"
SLURM_ACCOUNT = "nectec"
SLURM_API_VERSION = "v0.0.45"


# =========================================================
# Request model
# =========================================================

class JobRequest(BaseModel):
    name: str = Field(
        default="fastapi-test",
        min_length=1,
        max_length=128,
    )

    script: str = Field(
        ...,
        min_length=1,
    )

    cpus: int = Field(
        default=1,
        ge=1,
        le=24,
    )

    memory_mb: int = Field(
        default=512,
        ge=64,
        le=30000,
    )

    time_minutes: int = Field(
        default=1,
        ge=1,
        le=1440,
    )

    gpus: int = Field(
        default=0,
        ge=0,
        le=1,
    )

    gpu_mode: str | None = None

    mps_share: int = Field(
        default=0,
        ge=0,
        le=100,
    )


# =========================================================
# JWT / authentication
# =========================================================

def require_admin(
    identity: Annotated[dict, Depends(get_identity)]
):
    if identity["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator access required",
        )

    return identity


def require_admin_csrf(
    identity: Annotated[dict, Depends(require_csrf)]
):
    if identity["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator access required",
        )

    return identity



def get_slurm_token() -> str:
    """
    Generate a fresh Slurm JWT token.

    Important:
    We do not export SLURM_JWT into the shell environment.
    This avoids expired JWTs interfering with commands such as sacct.
    """

    try:
        result = subprocess.run(
            ["scontrol", "token"],
            capture_output=True,
            text=True,
            check=True,
        )

    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Unable to generate Slurm JWT",
                "error": exc.stderr.strip(),
            },
        )

    output = result.stdout.strip()

    if not output.startswith("SLURM_JWT="):
        raise HTTPException(
            status_code=500,
            detail="Unexpected response from scontrol token",
        )

    return output.split("=", 1)[1]


def slurm_headers() -> dict:
    token = get_slurm_token()

    return {
        "X-SLURM-USER-NAME": SLURM_USER,
        "X-SLURM-USER-TOKEN": token,
    }


# =========================================================
# slurmrestd HTTP helper
# =========================================================

def parse_gpu_count(value: str) -> int:
    if not value:
        return 0

    total = 0

    for item in value.split(","):
        item = item.strip()

        if not item.startswith("gpu:"):
            continue

        try:
            total += int(
                item.split(":")[-1]
            )
        except ValueError:
            continue

    return total

def parse_gres_count(
    value: str,
    resource: str,
) -> int:

    if not value:
        return 0

    prefix = f"{resource}:"

    for item in value.split(","):

        item = item.strip()

        if not item.startswith(prefix):
            continue

        count_part = (
            item[len(prefix):]
            .split("(", 1)[0]
        )

        try:
            return int(count_part)

        except ValueError:
            return 0

    return 0

def call_slurm(
    method: str,
    endpoint: str,
    payload=None,
):
    """
    Send a request to slurmrestd.
    """

    headers = slurm_headers()

    if payload is not None:
        headers["Content-Type"] = "application/json"

    url = f"{SLURMRESTD}{endpoint}"

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=payload,
            timeout=10,
        )

    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Unable to contact slurmrestd",
                "error": str(exc),
            },
        )

    try:
        data = response.json()

    except ValueError:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "slurmrestd returned non-JSON data",
                "response": response.text,
            },
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=data,
        )

    return data


# =========================================================
# Slurm value helpers
# =========================================================

def slurm_number(value):
    """
    Slurm REST often returns numbers like:

    {
        "set": true,
        "infinite": false,
        "number": 512
    }

    This helper returns only the actual number.
    """

    if isinstance(value, dict):
        if value.get("set"):
            return value.get("number")

        return None

    return value


def first_state(value):
    """
    Convert:

    ["RUNNING"]

    into:

    "RUNNING"
    """

    if isinstance(value, list):
        if value:
            return value[0]

        return None

    return value


def get_tres_count(items, tres_type, name=""):
    for item in items or []:
        if (
            item.get("type") == tres_type
            and item.get("name", "") == name
        ):
            return item.get("count", 0)

    return 0

def get_tres_string_count(value: str, resource: str) -> int:
    if not value:
        return 0

    prefix = f"{resource}="

    for item in str(value).split(","):
        item = item.strip()

        if not item.startswith(prefix):
            continue

        try:
            return int(
                item[len(prefix):]
            )
        except ValueError:
            return 0

    return 0
# =========================================================
# Clean scheduler job response
# =========================================================

def summarize_scheduler_job(job: dict):

    tres_requested = (
        job.get("tres_req_str") or ""
    )

    tres_allocated = (
        job.get("tres_alloc_str") or ""
    )


    gpu_requested = get_tres_string_count(
        tres_requested,
        "gres/gpu",
    )

    gpu_allocated = get_tres_string_count(
        tres_allocated,
        "gres/gpu",
    )


    mps_requested = get_tres_string_count(
        tres_requested,
        "gres/mps",
    )

    mps_allocated = get_tres_string_count(
        tres_allocated,
        "gres/mps",
    )


    return {
        "job_id": job.get("job_id"),
        "name": job.get("name"),
        "user": job.get("user_name"),
        "account": job.get("account"),

        "state": first_state(
            job.get("job_state")
        ),

        "state_reason": job.get(
            "state_reason"
        ),

        "partition": job.get(
            "partition"
        ),

        "nodes": job.get(
            "nodes"
        ),

        "cpus": slurm_number(
            job.get("cpus")
        ),

        "cpus_per_task": slurm_number(
            job.get("cpus_per_task")
        ),

        "tasks": slurm_number(
            job.get("tasks")
        ),

        "memory_mb": slurm_number(
            job.get("memory_per_node")
        ),

        "time_minutes": slurm_number(
            job.get("time_limit")
        ),

        "work_dir": job.get(
            "current_working_directory"
        ),

        "stdout": job.get(
            "stdout_expanded"
        ),

        "stderr": job.get(
            "stderr_expanded"
        ),

        "tres_requested":
            tres_requested,

        "tres_allocated":
            tres_allocated,

        "gpus_requested":
            gpu_requested,

        "gpus_allocated":
            gpu_allocated,

        "mps_requested":
            mps_requested,

        "mps_allocated":
            mps_allocated,

        "submit_time": slurm_number(
            job.get("submit_time")
        ),

        "start_time": slurm_number(
            job.get("start_time")
        ),

        "end_time": slurm_number(
            job.get("end_time")
        ),
    }

# =========================================================
# Clean accounting-history response
# =========================================================

def format_timestamp(timestamp):
    if timestamp in (None, 0):
        return None

    try:
        return datetime.fromtimestamp(timestamp).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def summarize_history_job(job: dict):
    state = job.get("state", {})

    state_value = first_state(
        state.get("current")
    )

    time_data = job.get(
        "time",
        {},
    )

    required = job.get(
        "required",
        {},
    )

    memory = required.get(
        "memory_per_node",
        {},
    )

    tres = job.get(
        "tres",
        {},
    )

    requested_tres = tres.get(
        "requested",
        [],
    )

    allocated_tres = tres.get(
        "allocated",
        [],
    )

    gpu_requested = get_tres_count(
        requested_tres,
        "gres",
        "gpu",
    )

    gpu_allocated = get_tres_count(
        allocated_tres,
        "gres",
        "gpu",
    )

    mps_requested = get_tres_count(
        requested_tres,
        "gres",
        "mps",
    )

    mps_allocated = get_tres_count(
        allocated_tres,
        "gres",
        "mps",
    )

    return {
        "job_id": job.get("job_id"),
        "name": job.get("name"),
        "user": job.get("user"),
        "account": job.get("account"),

        "state": state_value,
        "state_reason": state.get("reason"),

        "partition": job.get("partition"),
        "nodes": job.get("nodes"),

        "cpus": required.get("CPUs"),

        "memory_mb": slurm_number(
            memory
        ),

        "gpus_requested": gpu_requested,
        "gpus_allocated": gpu_allocated,

        "mps_requested": mps_requested,
        "mps_allocated": mps_allocated,

        "time_limit_minutes": slurm_number(
            time_data.get("limit")
        ),

        "elapsed_seconds": time_data.get(
            "elapsed"
        ),

        "submit_time": format_timestamp(
            time_data.get("submission")
        ),

        "eligible_time": format_timestamp(
            time_data.get("eligible")
        ),

        "start_time": format_timestamp(
            time_data.get("start")
        ),

        "end_time": format_timestamp(
            time_data.get("end")
        ),

        "restart_count": job.get(
            "restart_cnt"
        ),

        "stdout": job.get(
            "stdout_expanded"
        ),

        "working_directory": job.get(
            "working_directory"
        ),
    }

# =========================================================
# Root / health check
# =========================================================

@app.get("/")
def root():
    return {
        "message": "Slurm Portal API is running",
        "slurmrestd": SLURMRESTD,
        "slurm_user": SLURM_USER,
        "slurm_account": SLURM_ACCOUNT,
        "api_version": SLURM_API_VERSION,
    }


# =========================================================
# Submit job
# =========================================================

@app.post("/jobs")
def submit_job(
    job: JobRequest,
    identity: Annotated[dict, Depends(require_admin_csrf)],
):

    payload = {
        "script": job.script,

        "job": {
            "name": job.name,

            "account": SLURM_ACCOUNT,

            "environment": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            ],

            "current_working_directory": "/tmp",

            "tasks": 1,

            "cpus_per_task": job.cpus,

            "memory_per_node": {
                "set": True,
                "infinite": False,
                "number": job.memory_mb,
            },

            "time_limit": {
                "set": True,
                "infinite": False,
                "number": job.time_minutes,
            },
        },
    }

    # # Add GPU request only when requested
    # if job.gpus > 0:
    #     payload["job"]["tres_per_node"] = f"gres/gpu={job.gpus}"

    # -----------------------------------------------------
    # GPU / MPS resource request
    # -----------------------------------------------------

    # Backward compatibility with the existing frontend.
    # If gpu_mode is not supplied, continue using "gpus".
    if job.gpu_mode is None:
        if job.gpus > 0:
            payload["job"]["tres_per_node"] = f"gres/gpu={job.gpus}"

    else:
        gpu_mode = job.gpu_mode.strip().lower()

        if gpu_mode == "none":
            if job.gpus > 0 or job.mps_share > 0:
                raise HTTPException(
                    status_code=400,
                    detail="gpu_mode=none cannot request GPU or MPS resources",
                )

        elif gpu_mode == "whole":
            if job.mps_share > 0:
                raise HTTPException(
                    status_code=400,
                    detail="Whole GPU mode cannot be combined with an MPS share",
                )

            payload["job"]["tres_per_node"] = "gres/gpu=1"

        elif gpu_mode == "mps":
            if job.gpus > 0:
                raise HTTPException(
                    status_code=400,
                    detail="MPS mode cannot be combined with a whole GPU request",
                )

            if job.mps_share < 1:
                raise HTTPException(
                    status_code=400,
                    detail="mps_share must be between 1 and 100",
                )

            payload["job"]["tres_per_node"] = (
                f"gres/mps={job.mps_share}"
            )

        else:
            raise HTTPException(
                status_code=400,
                detail="gpu_mode must be one of: none, whole, mps",
            )

    data = call_slurm(
        method="POST",
        endpoint=f"/slurm/{SLURM_API_VERSION}/job/submit",
        payload=payload,
    )

    return {
        "job_id": data.get("job_id"),
        "step_id": data.get("step_id"),
        "message": "Job submitted successfully",
        "errors": data.get("errors", []),
        "warnings": data.get("warnings", []),
    }

# =========================================================
# Active / scheduler jobs
# =========================================================

@app.get("/jobs/active")
def get_active_jobs(
    identity: Annotated[dict, Depends(require_admin)],
):

    data = call_slurm(
        method="GET",
        endpoint=(
            f"/slurm/"
            f"{SLURM_API_VERSION}/"
            f"jobs/"
        ),
    )

    jobs = data.get("jobs", [])

    active_states = {
        "PENDING",
        "RUNNING",
        "CONFIGURING",
        "COMPLETING",
        "SUSPENDED",
    }

    active_jobs = []

    for job in jobs:
        state = first_state(
            job.get("job_state")
        )

        if state in active_states:
            active_jobs.append(job)

    return {
        "count": len(active_jobs),

        "jobs": [
            summarize_scheduler_job(job)
            for job in active_jobs
        ],
    }


# =========================================================
# Accounting job history
# =========================================================

@app.get("/jobs/history")
def get_job_history(
    identity: Annotated[dict, Depends(require_admin)],
):

    start_time = int(time.time()) - (7 * 24 * 60 * 60)

    data = call_slurm(
        method="GET",
        endpoint=(
            f"/slurmdb/"
            f"{SLURM_API_VERSION}/"
            f"jobs/"
            f"?start_time={start_time}"
            f"&users={SLURM_USER}"
            f"&skip_steps=true"
        ),
    )

    jobs = data.get("jobs", [])

    terminal_states = {
        "COMPLETED",
        "CANCELLED",
        "FAILED",
        "TIMEOUT",
        "OUT_OF_MEMORY",
        "NODE_FAIL",
        "PREEMPTED",
        "BOOT_FAIL",
        "DEADLINE",
    }

    history = []

    for job in jobs:

        summary = summarize_history_job(job)

        if summary["state"] in terminal_states:
            history.append(summary)


    # Newest jobs first
    history.sort(
        key=lambda job: job.get("submit_time") or "",
        reverse=True,
    )


    return {
        "count": len(history),
        "jobs": history,
    }

# =========================================================
# Web UI
# =========================================================

@app.get("/ui", response_class=HTMLResponse)
def web_ui(
    identity: Annotated[dict, Depends(require_admin)],
):

    path = Path(
        "/home/lailai/slurm-portal/frontend.html"
    )

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="frontend.html not found",
        )

    return path.read_text(
        encoding="utf-8"
    )

# =========================================================
# Get one job
# =========================================================


# =========================================================
# Cluster / node status
# =========================================================

@app.get("/cluster/node")
def get_cluster_node(
    identity: Annotated[dict, Depends(require_admin)],
):

    data = call_slurm(
        method="GET",
        endpoint=(
            f"/slurm/"
            f"{SLURM_API_VERSION}/"
            f"nodes/"
        ),
    )

    nodes = data.get(
        "nodes",
        [],
    )

    node = next(
        (
            item
            for item in nodes
            if item.get("name") == "lai512"
        ),
        None,
    )

    if node is None:
        raise HTTPException(
            status_code=404,
            detail="Slurm node lai512 not found",
        )

    total_cpus = node.get(
        "cpus",
        0,
    )

    allocated_cpus = node.get(
        "alloc_cpus",
        0,
    )

    total_memory = node.get(
        "real_memory",
        0,
    )

    allocated_memory = node.get(
        "alloc_memory",
        0,
    )

    gpu_total = parse_gres_count(
        node.get("gres", ""),
        "gpu",
    )

    gpu_used = parse_gres_count(
        node.get("gres_used", ""),
        "gpu",
    )


    mps_total = parse_gres_count(
        node.get("gres", ""),
        "mps",
    )

    mps_used = parse_gres_count(
        node.get("gres_used", ""),
        "mps",
    )

    return {
        "name": node.get("name"),

        "hostname": node.get(
            "hostname"
        ),

        "state": first_state(
            node.get("state")
        ),

        "partition": (
            node.get("partitions", [None])[0]
            if node.get("partitions")
            else None
        ),

        "cpus": {
            "total": total_cpus,
            "allocated": allocated_cpus,
            "idle": node.get(
                "alloc_idle_cpus",
                0,
            ),
        },

        "memory_mb": {
            "total": total_memory,
            "allocated": allocated_memory,
            "available_for_slurm": max(
                0,
                total_memory - allocated_memory,
            ),
            "host_free": slurm_number(
                node.get("free_mem")
            ),
        },

        "gpus": {
            "total": gpu_total,
            "allocated": gpu_used,
            "available": (
                0
                if mps_used > 0
                else max(
                    0,
                    gpu_total - gpu_used,
                )
            ),
        },

        "mps": {
            "total": mps_total,
            "allocated": mps_used,
            "available": (
                0
                if gpu_used > 0
                else max(
                    0,
                    mps_total - mps_used,
                )
            ),
        },

        "gres": node.get("gres"),
        "gres_used": node.get(
            "gres_used"
        ),

        "slurm_version": node.get(
            "version"
        ),

        "operating_system": node.get(
            "operating_system"
        ),
    }

@app.get("/jobs/{job_id}")
def get_job(
    job_id: int,
    identity: Annotated[dict, Depends(require_admin)],
):

    if job_id < 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "job_id must be greater "
                "than 0"
            ),
        )

    data = call_slurm(
        method="GET",
        endpoint=(
            f"/slurm/"
            f"{SLURM_API_VERSION}/"
            f"job/{job_id}"
        ),
    )

    jobs = data.get(
        "jobs",
        [],
    )

    if not jobs:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Job {job_id} "
                f"not found"
            ),
        )

    return summarize_scheduler_job(
        jobs[0]
    )
# =========================================================
# Cancel job
# =========================================================

@app.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int,
    identity: Annotated[dict, Depends(require_admin_csrf)],
):

    if job_id < 1:
        raise HTTPException(
            status_code=400,
            detail="job_id must be greater than 0",
        )

    data = call_slurm(
        method="DELETE",
        endpoint=(
            f"/slurm/"
            f"{SLURM_API_VERSION}/"
            f"job/{job_id}"
        ),
    )

    return {
        "job_id": job_id,
        "message": "Cancel request sent successfully",
        "errors": data.get("errors", []),
        "warnings": data.get("warnings", []),
    }


# =========================================================
# Requeue job
# =========================================================

@app.post("/jobs/{job_id}/requeue")
def requeue_job(
    job_id: int,
    identity: Annotated[dict, Depends(require_admin_csrf)],
):

    if job_id < 1:
        raise HTTPException(
            status_code=400,
            detail="job_id must be greater than 0",
        )

    data = call_slurm(
        method="GET",
        endpoint=(
            f"/slurm/"
            f"{SLURM_API_VERSION}/"
            f"job/{job_id}/requeue"
        ),
    )

    return {
        "job_id": job_id,
        "message": "Requeue request sent successfully",
        "errors": data.get("errors", []),
        "warnings": data.get("warnings", []),
    }
# =========================================================
# Job output
# =========================================================

@app.get("/jobs/{job_id}/output")
def get_job_output(
    job_id: int,
    identity: Annotated[dict, Depends(require_admin)],
):

    if job_id < 1:
        raise HTTPException(
            status_code=400,
            detail="job_id must be greater than 0",
        )

    # Our submitted Slurm jobs currently use the default
    # output path: /tmp/slurm-<job_id>.out
    path = Path(f"/tmp/slurm-{job_id}.out")

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Output file not found for job {job_id}",
        )

    try:
        content = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unable to read job output: {exc}",
        )

    return {
        "job_id": job_id,
        "output_file": str(path),
        "output": content,
    }