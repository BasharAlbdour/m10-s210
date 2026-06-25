# Stretch Thu — Multi-Service Coordinator (Honors Track)
> Honors Track — for learners who have completed all core Module 10
> assignments, are On Track or Advanced, and are attending consistently.

Decompose the Module 10 Lab's monolithic backend into three downstream
microservices (NLP, KG, RAG), add a query-classifier service, and add
a coordinator service that fans out to the selected downstream(s) and
handles partial failure gracefully.

> Read the full spec on the cohort site:
> <https://LevelUp-Applied-AI.github.io/aispire-14005-pages/modules/module-10/14bc816e>

---

## Architecture

```
                        ┌─────────────────┐
         POST /answer   │                 │
User ──────────────────▶│   coordinator   │
                        │   :8000         │
                        └────────┬────────┘
                                 │ 1. POST /classify
                        ┌────────▼────────┐
                        │  classifier_svc │
                        │  :8001          │
                        └────────┬────────┘
                                 │ 2. fan-out to selected services
               ┌─────────────────┼─────────────────┐
               ▼                 ▼                 ▼
        ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
        │   nlp_svc   │  │   kg_svc    │  │   rag_svc   │
        │   :8002     │  │   :8003     │  │   :8004     │
        │  /extract   │  │  /kg/query  │  │ /rag/answer │
        └─────────────┘  └─────────────┘  └─────────────┘
```

**Flow:** The coordinator receives a question, asks the classifier which service(s) should answer, fans out concurrently to each selected service via `httpx.AsyncClient` with per-call timeouts (5 s for NLP/KG, 10 s for RAG), aggregates the responses, and returns a single `AnswerResponse`. If any upstream fails or times out, the coordinator returns `200` with `"partial": true` rather than propagating a 5xx.

---

## Coordinator Design

The coordinator implements a classify → fan-out → aggregate pattern. On each `POST /answer` request it first calls `classifier_svc` to get a ranked list of routes with confidence scores. It then fans out concurrently to every selected downstream using `asyncio.gather` with independent `httpx.AsyncClient` instances, each governed by a per-call timeout. Results are collected into a structured `AnswerResponse`: `results` maps each service name to its payload (or `null` on failure), `responded` lists the services that returned successfully, and `partial` is `true` when at least one upstream failed but at least one succeeded. If every upstream fails the coordinator returns `503`. All inbound requests are logged as a single structured JSON line recording which upstreams were selected, which responded, and total latency in milliseconds — a format Module 11 can consume directly.

---

## Microservice Split — Cost / Benefit

Splitting the monolithic Lab backend into three independent microservices buys independent deployability and fault isolation: a crash in `rag_svc` does not take down `kg_svc` or `nlp_svc`, and each service can be scaled, redeployed, or swapped out without touching the others. It also makes the routing logic explicit — the classifier's `routes` list is a machine-readable record of which tool was selected and why, which is the direct equivalent of an agent's tool-selection step. The cost is real: shared concerns (Pydantic models, `/healthz`, structured errors) must be duplicated across all three services, the Compose topology grows from one backend to five containers, and inter-service calls add network hops and timeout surface area that a monolith avoids entirely. For a production system these trade-offs are worth it at scale; for a small domain like this one they add meaningful operational overhead.

---

## Setup

```bash
git clone https://github.com/<your-username>/m10-s210.git
cd m10-s210
git checkout stretch-10-thu-coordinator
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Running the stack

```bash
docker compose up -d --build
```

All 5 services start with healthchecks. The coordinator waits for all four downstreams to be healthy before starting (`condition: service_healthy`).

Verify everything is up:

```bash
docker compose ps
```

Expected output — all 5 containers `healthy`:

```
NAME                        STATUS
m10-s210-classifier_svc-1   healthy
m10-s210-nlp_svc-1          healthy
m10-s210-kg_svc-1           healthy
m10-s210-rag_svc-1          healthy
m10-s210-coordinator-1      healthy
```

---

## End-to-end demo

### RAG-routed question

```bash
curl -s -X POST http://localhost:8000/answer \
  -H "Content-Type: application/json" \
  -d '{"question": "how do I cook pasta"}' | python -m json.tool
```

Response:

```json
{
    "results": {
        "rag_svc": {
            "answer": "A mocked grounded answer [1].",
            "citations": [{"chunk_id": 1, "score": 0.9}],
            "confidence": 0.9,
            "service": "rag_svc"
        }
    },
    "partial": false,
    "responded": ["rag_svc"]
}
```

### KG-routed question

```bash
curl -s -X POST http://localhost:8000/answer \
  -H "Content-Type: application/json" \
  -d '{"question": "find recipes with chicken ingredient"}' | python -m json.tool
```

Response:

```json
{
    "results": {
        "kg_svc": {
            "cypher": "MATCH (n) RETURN n",
            "rows": [],
            "count": 0,
            "service": "kg_svc"
        }
    },
    "partial": false,
    "responded": ["kg_svc"]
}
```

---

## Partial-failure test

Stop `rag_svc` to simulate a downstream outage:

```bash
docker compose stop rag_svc
```

Send a hybrid question that routes to both `kg_svc` and `rag_svc`:

```bash
curl -s -X POST http://localhost:8000/answer \
  -H "Content-Type: application/json" \
  -d '{"question": "how do I find recipes with chicken ingredient"}' | python -m json.tool
```

Response with `rag_svc` down:

```json
{
    "results": {
        "kg_svc": {
            "cypher": "MATCH (n) RETURN n",
            "rows": [],
            "count": 0,
            "service": "kg_svc"
        },
        "rag_svc": null
    },
    "partial": true,
    "responded": ["kg_svc"]
}
```

`"partial": true` — `kg_svc` responded, `rag_svc` failed, coordinator returned `200` rather than `503`.

Bring `rag_svc` back:

```bash
docker compose start rag_svc
```

---

## Structured request logging

Each inbound `POST /answer` emits one JSON log line on the coordinator:

```json
{
  "event": "answer_request",
  "question_len": 42,
  "selected": ["kg_svc", "rag_svc"],
  "responded": ["kg_svc"],
  "partial": true,
  "total_latency_ms": 312.5
}
```

View live:

```bash
docker compose logs coordinator -f
```

---

## Running the tests

```bash
pytest
```

All 6 tests pass hermetically (no Docker required — upstreams are mocked via `httpx`).

---

## Tearing down

```bash
docker compose down
```

---

## Submission

PR URL pasted into TalentLMS → Module 10 → Stretch Thu.

---

## License

This repository is provided for educational use only. See
[LICENSE](LICENSE) for terms.
