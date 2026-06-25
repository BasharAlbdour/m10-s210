"""Multi-service coordinator — Stretch Thu (Honors Track).

The coordinator exposes a single POST /answer endpoint. On each call it:
1. Calls the classifier service to identify which downstream service(s)
   should answer the question.
2. Fans out to the selected service(s) via httpx.AsyncClient with a
   per-call 5-second timeout.
3. Aggregates the responses and returns a single AnswerResponse.
4. If any upstream returns a 5xx or times out, the coordinator returns
   200 with `partial: true` and a per-service attribution payload —
   never a 5xx that would lose the working upstream's response.
"""
from fastapi import FastAPI, HTTPException
import asyncio
import json
import logging
import time
import httpx

try:
    from coordinator.models import AnswerRequest, AnswerResponse
    from coordinator.upstream import call_upstream
except ImportError:
    from models import AnswerRequest, AnswerResponse
    from upstream import call_upstream

logger = logging.getLogger(__name__)

app = FastAPI(title="Stretch Thu — Multi-Service Coordinator")
SERVICE_REGISTRY = {
    "nlp_svc":  ("http://nlp_svc:8000/extract",    5.0),   
    "kg_svc":   ("http://kg_svc:8000/kg/query",     5.0),  
    "rag_svc":  ("http://rag_svc:8000/rag/answer",  10.0),  
}
CLASSIFIER_URL = "http://classifier_svc:8000/classify"


@app.post("/answer",response_model=AnswerResponse)
async def answer(req: AnswerRequest):
    """Classify → fan out → aggregate → respond.

    Returns AnswerResponse. `partial: true` iff one or more upstreams
    failed or timed out.
    """
    # TODO: set response_model=AnswerResponse on the decorator above.
    # TODO: ask the classifier which downstream services should answer.
    # TODO: fan out to each selected service over httpx.AsyncClient with
    #       a per-call timeout (longer for the generation service).
    # TODO: collect each upstream's outcome and record which services
    #       responded successfully.
    # TODO: set `partial` when at least one upstream failed and at least
    #       one succeeded.
    # TODO: return AnswerResponse with `results`, `partial`, and
    #       `responded` populated.
    request_start=time.perf_counter() * 1000
    routes = []
    async with httpx.AsyncClient() as client:
        try:
            clf_resp = await client.post(
                CLASSIFIER_URL,
                json={"question": req.question},
                timeout=httpx.Timeout(5.0),
            )
            clf_resp.raise_for_status()
            routes = clf_resp.json().get("routes", [])
        except Exception as e:
            logger.warning(json.dumps({
                "event": "classifier_error",
                "error": str(e),
            }))
            routes = [{"service": "rag_svc", "confidence": 0.0}]

    selected = [r["service"] for r in routes if r["service"] in SERVICE_REGISTRY]
    if not selected:
        selected = ["rag_svc"]

    tasks = []
    for svc_name in selected:
        url, timeout_s = SERVICE_REGISTRY[svc_name]
        tasks.append(
            call_upstream(svc_name, url, {"question": req.question}, timeout_s=timeout_s)
        )

    upstream_results = await asyncio.gather(*tasks)

    results = {}
    responded = []

    for res in upstream_results:
        svc = res["service"]
        if res["status"] == "ok":
            results[svc] = res.get("payload")
            responded.append(svc)
        else:
            results[svc] = None

    partial = len(responded) > 0 and len(responded) < len(selected)

    if len(responded) == 0:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "all upstreams failed",
                "services": selected,
            },
        )

    total_ms = round(time.perf_counter() * 1000 - request_start, 2)
    logger.info(json.dumps({
        "event": "answer_request",
        "question_len": len(req.question),
        "selected": selected,
        "responded": responded,
        "partial": partial,
        "total_latency_ms": total_ms,
    }))

    return AnswerResponse(results=results, partial=partial, responded=responded)
    


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
