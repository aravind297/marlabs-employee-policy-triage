# Marlabs GenAI Spring Boot Candidate Assessment

Employee policy and reimbursement triage. This repository contains a Spring Boot public API and a Python policy/document service, the original synthetic policy corpus, all eight requested synthetic documents, tests, and example requests/responses.

## Architecture and trust boundaries

- **Spring Boot (Java 17, Spring Boot 3.3.5)** owns public endpoints, simulated caller lookup, request/manifest validation, batch ordering, exact-byte duplicate detection, public response construction, and trace logging.
- **Python (FastAPI)** extracts UTF-8 TXT and text-based PDF content, extracts fields with source quotations, filters policy records, retrieves eligible evidence, and uses a deterministic offline model double. No API key or paid model is needed.
- The `X-Caller-Id` header is the only caller identity input. Tenant and role are resolved from the Spring Boot registry and passed to Python as trusted internal context. Document claims about identity are never used for access decisions.
- Policy data stays in `python-service/data/policies.json`, separate from application logic. Original IDs, metadata and passage text are preserved. The injection-example record is retained in the corpus but quarantined by content marker and is never eligible for generation or citations.
- No approval, payment, or employee messaging functionality exists. Every item has `review_required.required=true` when processed; a technical failure also gets a review-required result.

## Prerequisites

- Java 17+
- Maven 3.9+
- Python 3.11+ (tested with Python 3.13)

## Run locally

Terminal 1 — Python service:

```bash
cd python-service
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Terminal 2 — Spring Boot API:

```bash
cd spring-api
mvn test
mvn spring-boot:run
```

Python tests:

```bash
cd python-service
python -m pip install -r requirements.txt
python -m pytest -q
```

The Python tests pass in the supplied implementation environment. Maven was not installed in the authoring environment, so Java tests and a full end-to-end runtime could not be executed there; run the Maven commands above to verify them in a Maven-enabled environment.

Configuration:

- `PYTHON_SERVICE_URL` (default `http://localhost:8000`)
- `PYTHON_CONNECT_TIMEOUT_MS` (default `1000`)
- `PYTHON_READ_TIMEOUT_MS` (default `5000`)
- Upload limits are configured in `spring-api/src/main/resources/application.yml`.

There are no automatic HTTP/model retries. The offline model performs one generation attempt. Timeouts, provider unavailability, malformed output and service failures are technical failures, not `INSUFFICIENT_EVIDENCE`.

## Public API

### `POST /answer`

Headers: `Content-Type: application/json`, `X-Caller-Id: atlas-employee-01`

```bash
curl -sS http://localhost:8080/answer \
  -H 'Content-Type: application/json' \
  -H 'X-Caller-Id: atlas-employee-01' \
  -d '{"question":"What is my annual certification reimbursement limit?","as_of":"2026-09-21"}'
```

Business outcomes use HTTP 200 and this shape:

```json
{"status":"ANSWERED","answer":"The annual certification reimbursement limit is INR 25000.","citations":[{"chunk_id":"atlas-cert-current","quote":"The annual certification reimbursement limit for employees is INR 25000."}]}
```

Possible statuses: `ANSWERED`, `INSUFFICIENT_EVIDENCE`, `CONFLICT`. For insufficient evidence, `answer` is `null` and citations are empty. For conflicts, `answer` is `null` and citations contain eligible passages showing the disagreement. Citation `quote` values are verbatim policy text; `chunk_id` is the policy ID.

Known callers:

| X-Caller-Id | Tenant | Role |
|---|---|---|
| `atlas-employee-01` | Atlas | employee |
| `atlas-contractor-01` | Atlas | contractor |
| `boreal-employee-01` | Boreal | employee |

### `POST /batches`

`multipart/form-data`: one `metadata` part containing JSON, plus repeated `files` parts. Original uploaded filenames must exactly match manifest filenames. Results preserve manifest order.

```bash
curl -sS http://localhost:8080/batches \
  -H 'X-Caller-Id: atlas-employee-01' \
  -F 'metadata={"batch_id":"demo-01","as_of":"2026-09-21","documents":[{"document_id":"request-01","filename":"request-01.txt"},{"document_id":"request-02","filename":"request-02.pdf"},{"document_id":"request-03","filename":"request-03.txt"},{"document_id":"request-04","filename":"request-04.txt"},{"document_id":"request-05","filename":"request-05.txt"},{"document_id":"request-06","filename":"request-06.txt"},{"document_id":"request-07","filename":"request-07.txt"},{"document_id":"request-08","filename":"request-08.txt"}]}' \
  -F 'files=@../examples/requests/request-01.txt;type=text/plain' \
  -F 'files=@../examples/requests/request-02.pdf;type=application/pdf' \
  -F 'files=@../examples/requests/request-03.txt;type=text/plain' \
  -F 'files=@../examples/requests/request-04.txt;type=text/plain' \
  -F 'files=@../examples/requests/request-05.txt;type=text/plain' \
  -F 'files=@../examples/requests/request-06.txt;type=text/plain' \
  -F 'files=@../examples/requests/request-07.txt;type=text/plain' \
  -F 'files=@../examples/requests/request-08.txt;type=text/plain'
```

Nested batch response shapes are chosen as follows:

- `summary`: `{total, completed, failed}`
- `results[]`: `document_id`, `processing_status`, `extracted`, `field_evidence`, `policy`, `review_required`, `duplicate_of`, `error`
- `extracted`: `{benefit, amount, currency, reference}`; unresolved fields are `null`.
- `field_evidence`: object keyed by field, each value an array of `{quote}` objects. Empty array means no source quotation supports that value.
- `policy`: the `/answer` shape or `null` if the benefit could not be identified or processing failed.
- `review_required`: `{required: true, issues: [...]}`.
- `duplicate_of`: earlier document ID for exact byte-identical files, otherwise `null`.
- `error`: `null` on completion; otherwise `{code, message}`.

Manifest errors fail the whole request with non-2xx. Empty files and item-level failures return `FAILED` for that item while other entries continue. Example stable codes include `EMPTY_FILE`, `UNREADABLE_FILE`, `UNSUPPORTED_FILE_TYPE`, `DOCUMENT_ANALYSIS_FAILURE`, `FILE_PART_COUNT_MISMATCH`, `DUPLICATE_DOCUMENT_ID`, and `FILE_MANIFEST_MISMATCH`.

## Expected behavior for supplied cases

- `request-01`: certification; amount INR 18000; current Atlas employee limit INR 25000; payment remains a human decision.
- `request-02`: home-office amount INR 14000; two eligible Atlas policies disagree (INR 12000 and INR 15000), so policy status is `CONFLICT`.
- `request-03`: amount remains `null` because INR 22000 and INR 28000 conflict in the document; human review is required.
- `request-04`: wellness benefit has no supplied policy; policy status is `INSUFFICIENT_EVIDENCE`.
- `request-05`: the text's Boreal claim and prompt injection do not alter Atlas caller context. The Atlas certification policy is used.
- `request-06`: exact byte duplicate of request-01, returned as a separate result with `duplicate_of: "request-01"`.
- `request-07`: manager approval was not obtained before external training was booked; flag for human review, with no payable decision.
- `request-08`: zero-byte input produces `FAILED`; other items continue.

## Tests and known limitations

Python tests cover tenant/role scope, date intervals, exact citations, conflicts, injection quarantine, ambiguous amount extraction, empty files, and timeout/unavailable/malformed model doubles with a single attempt. Java tests cover unknown callers, invalid dates, manifest filename mismatch, and a mixed completed/failed batch. Test doubles exercise the model provider failure contract; there is no hosted model configured by default.

Not covered exhaustively: adversarial PDF parser fuzzing, concurrent load/race behavior, all Unicode edge cases, real hosted model behavior, and production authentication. OCR is intentionally not included; scanned PDFs without extractable text fail. The policy-topic matcher is intentionally small and transparent for this synthetic corpus, not a general-purpose semantic search engine.

## Decision note

**Choice:** keep caller identity resolution and public contract enforcement in Spring Boot, while Python owns document parsing and policy retrieval. This honors the assessment's service boundary and prevents uploaded content from selecting its own tenant or role. Policy metadata and text live in a separate JSON corpus, and filtering happens before the model adapter sees passages.

**Rejected alternative:** one Python-only public API would be simpler, but it would not meet the requirement that Spring Boot own the public API and caller context. A real hosted LLM was also not selected as the default because tests must run offline without keys, and the policy corpus is small enough for deterministic evidence handling.

**Main limitation:** benefit identification and text extraction use transparent rules rather than robust semantic retrieval/OCR. The model double is deterministic; a real provider would require additional security, quality, privacy, latency, and cost validation.

**Verification status:** The Python automated test suite and Spring Boot Maven test suite have been run successfully. The public `/answer` and `/batches` endpoints have also been verified through Postman, including normal processing, policy conflicts, tenant isolation, duplicate documents, and failure handling.

## Production design note (under 400 words)

For 10,000 requests/day containing personal information and depending on a slow, partially documented approval system, I would deploy Spring Boot and Python as separate containerized services behind a managed API gateway, initially in a private network with autoscaling based on latency and queue depth. I would keep the synchronous policy-answer path bounded and move long reimbursement review orchestration to a durable queue only after clarifying response-time expectations. The application would still never approve or pay a claim without an explicit authorized human workflow.

The first security changes would be real OIDC authentication, server-side tenant/role authorization, least-privilege service identity, TLS/mTLS between services, secrets management, encryption at rest, strict upload limits and malware scanning, safe PDF parsing, PII minimization/redaction, retention/deletion rules, and an audit trail that excludes full document text and secrets. Prompt injection would remain untrusted input; only allowlisted policy passages could be supplied to a model. A hosted model would need contractual data-use controls and evaluation before production use.

Operational priorities would be bounded timeouts, no blind retries for generation or approval side effects, idempotency keys, circuit breakers, backpressure, dead-letter handling, trace IDs, structured metrics, alerts, and dashboards for failure rate, latency, unresolved evidence, policy conflicts, and manual-review backlog. I would version policy records and retrieval logic and add regression evaluations before policy changes.

Before committing to a delivery date, I would clarify the approval-system API and its consistency guarantees, authentication and authorization model, rate limits, idempotency support, timeout/error semantics, SLA, escalation and cancellation rules, data residency/retention requirements, expected document formats and volumes, policy ownership and conflict resolution, acceptable model accuracy, and who signs off on human-review outcomes. The largest schedule risk is the undocumented approval integration, not the initial API code.
