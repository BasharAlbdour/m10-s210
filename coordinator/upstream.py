"""httpx.AsyncClient helpers — per-call timeout enforcement.

Catches a common mistake where learners set the timeout at the session
level (once across the whole AsyncClient lifecycle) instead of per
.get/.post call. The session-level timeout still applies but does not
fire per call, so slow upstreams can starve faster ones.
"""
import time
import logging
import httpx
import json
logger = logging.getLogger(__name__)

async def call_upstream(service: str, url: str, payload: dict, timeout_s: float = 5.0):
    """Call one upstream service. Returns an UpstreamResult-shaped dict.

    Per-call timeout via `httpx.Timeout(timeout_s)` on `.post`.
    """
    # TODO:
    # 1. Record start_ms = time.perf_counter() * 1000.
    # 2. async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
    #        try POST url with json=payload.
    # 3. On TimeoutException → return {"service", "status": "timeout", ...}.
    # 4. On any other exception → return {"service", "status": "error", "error": str(e), ...}.
    # 5. On success → return {"service", "status": "ok", "payload": r.json(), ...}.
    start_ms = time.perf_counter() * 1000
    
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            r = await client.post(url, json=payload)
            latency_ms = time.perf_counter() * 1000 - start_ms
            result = {
                "service": service,
                "status": "ok",
                "latency_ms": round(latency_ms, 2),
                "payload": r.json(),
                "error": None,
            }
    except httpx.TimeoutException:
        latency_ms = time.perf_counter() * 1000 - start_ms
        result = {
            "service": service,
            "status": "timeout",
            "latency_ms": round(latency_ms, 2),
            "payload": None,
            "error": "upstream timed out",
        }
    except Exception as e:
        latency_ms = time.perf_counter() * 1000 - start_ms
        result = {
            "service": service,
            "status": "error",
            "latency_ms": round(latency_ms, 2),
            "payload": None,
            "error": str(e),
        }

    logger.debug(
        json.dumps({
            "event": "upstream_call",
            "service": result["service"],
            "status": result["status"],
            "latency_ms": result["latency_ms"],
        })
    )
    return result

