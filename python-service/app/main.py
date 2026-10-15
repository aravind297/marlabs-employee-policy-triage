"""Policy retrieval and document analysis service. Offline/deterministic by design."""
from __future__ import annotations
import io, json, logging, re
from datetime import date
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pypdf import PdfReader
from app.model import OfflineModel, generate_once, ModelTimeout, ModelUnavailable, MalformedModelOutput

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("triage-python")
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "policies.json"
POLICIES = json.loads(DATA_PATH.read_text(encoding="utf-8"))
MODEL = OfflineModel()

class AnswerRequest(BaseModel):
    question: str = Field(min_length=1)
    as_of: date
    tenant: str
    role: str

class AnswerResponse(BaseModel):
    status: str
    answer: Optional[str]
    citations: list[dict]

app = FastAPI(title="Marlabs Triage Policy Service", version="1.0.0")

# A quarantined passage is retained verbatim in the corpus, but never eligible evidence.
INJECTION_MARKERS = ("system message:", "ignore all prior rules", "prompt-injection example")
TOPIC_TERMS = {
    "certification": ("certification", "certificate", "cert"),
    "home-office": ("home-office", "home office", "desk", "chair"),
    "travel": ("rail travel", "train travel", "business trip", "travel"),
    "training": ("external training", "training", "manager approval"),
}

def topic_for(text: str) -> Optional[str]:
    lowered = text.casefold()

    for topic in ("certification", "home-office", "training", "travel"):
        if any(term in lowered for term in TOPIC_TERMS[topic]):
            return topic
    return None

def is_safe_policy(p: dict) -> bool:
    lowered = p["text"].casefold()
    return not any(marker in lowered for marker in INJECTION_MARKERS)

def eligible_policies(tenant: str, role: str, as_of: date) -> list[dict]:
    found = []
    for p in POLICIES:
        if p["tenant"] != tenant or p["role"] != role or p["approval_state"] != "Approved":
            continue
        if not (date.fromisoformat(p["effective_from"]) <= as_of < date.fromisoformat(p["effective_to"])):
            continue
        if not is_safe_policy(p):
            continue
        found.append(p)
    return found

def matching_policies(topic: str, tenant: str, role: str, as_of: date) -> list[dict]:

    out = []
    for p in eligible_policies(tenant, role, as_of):
        p_topic = topic_for(p["text"])
        if p_topic == topic:
            out.append(p)
    return out

def amount_from_policy(text: str) -> Optional[str]:
    m = re.search(r"\bINR\s+[0-9,]+\b", text, re.IGNORECASE)
    return m.group(0).replace("  ", " ") if m else None

def answer_question(question: str, tenant: str, role: str, as_of: date) -> dict:
    topic = topic_for(question)
    if not topic:
        return {"status":"INSUFFICIENT_EVIDENCE", "answer":None, "citations":[]}
    policies = matching_policies(topic, tenant, role, as_of)
    # Only eligible, approved, tenant/role/date-scoped, non-injection passages enter generation.

    try:
        generated = generate_once(MODEL, question, topic, policies)

    except (ModelTimeout, ModelUnavailable, MalformedModelOutput) as exc:
        raise RuntimeError(type(exc).__name__) from exc

    by_id = {p["id"]: p for p in policies}
    citations = [{"chunk_id": cid, "quote": by_id[cid]["text"]} for cid in generated["citation_ids"]]
    return {"status": generated["status"], "answer": generated["answer"], "citations": citations}

def extract_text(filename: str, raw: bytes) -> str:
    if not raw:
        raise ValueError("EMPTY_FILE")
    lower = filename.casefold()
    if lower.endswith(".txt"):
        try:
            return raw.decode("utf-8-sig").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("UNREADABLE_FILE") from exc
    if lower.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(raw), strict=True)
            text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
            if not text:
                raise ValueError("UNREADABLE_FILE")
            return text
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("UNREADABLE_FILE") from exc
    raise ValueError("UNSUPPORTED_FILE_TYPE")

def quoted_match(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    return m.group(0).strip() if m else None


def evidence_quote(text: str, start: int, end: int) -> str:
    """Return a complete source sentence/clause containing the matched value.

    Quotes are sliced from the original document so field evidence remains verbatim;
    unlike a fixed-width window, this never cuts the beginning or end of a value's
    supporting sentence merely because the sentence is long.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)

    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    local_start = start - line_start
    local_end = end - line_start

    # Treat punctuation followed by whitespace and a sentence-start character as
    # a sentence boundary. This avoids splitting decimal values such as 18.50.
    boundaries = list(re.finditer(r"(?<=[.!?])\s+(?=[A-Z₹\"'])", line))
    sentence_start = 0
    sentence_end = len(line)

    for boundary in boundaries:

        if boundary.end() <= local_start:
            sentence_start = boundary.end()

        elif boundary.start() >= local_end:
            sentence_end = boundary.start()
            break

    quote = line[sentence_start:sentence_end].strip()
    # Defensive fallback: the quote must always contain the original matched value.

    if not quote or text[start:end] not in quote:
        quote = text[start:end]
    return quote


def extract_fields(text: str) -> tuple[dict, dict, list[str], Optional[str]]:

    reference_quote = quoted_match(text, r"^\s*Reference\s*:\s*[^\r\n]+")

    reference = None

    if reference_quote:
        candidate = reference_quote.split(":", 1)[1].strip()
        reference = candidate or None

    # Keep the exact source span for each distinct monetary amount. Different
    # values make the amount ambiguous, but each quote must clearly support it.
    amount_matches = list(re.finditer(r"\b(INR|Rs\.?|₹)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text, re.IGNORECASE))

    amounts = []

    for match in amount_matches:
        value = match.group(2).replace(",", "")

        if value not in [item[0] for item in amounts]:
            quote = evidence_quote(text, match.start(), match.end())
            amounts.append((value, match.group(1).upper(), quote))

    amount = amounts[0][0] if len(amounts) == 1 else None
    # Currency can remain supported even when the amount itself is ambiguous.
    currency = "INR" if amounts and all(item[1] in {"INR", "RS", "RS.", "₹"} for item in amounts) else None
    amount_evidence = [item[2] for item in amounts]

    # topic_for is intentionally limited to benefits with a policy topic. Recognize
    # gym membership as an extracted benefit without inventing a policy topic.
    benefit = topic_for(text)
    benefit_labels = {
        "certification": "certification reimbursement",
        "home-office": "home-office allowance",
        "travel": "rail travel",
        "training": "external training",
    }
    extracted_benefit = benefit_labels.get(benefit)
    benefit_quote = None

    if benefit:
        phrase = {
            "certification": r"[^.\n]*certification[^.\n]*",
            "home-office": r"[^.\n]*(?:home-office|home office|desk and chair)[^.\n]*",
            "travel": r"[^.\n]*travel[^.\n]*",
            "training": r"[^.\n]*training[^.\n]*",
        }[benefit]
        benefit_quote = quoted_match(text, phrase)

    elif re.search(r"\bgym\s+membership\b", text, re.IGNORECASE):
        extracted_benefit = "gym membership"
        benefit_quote = quoted_match(text, r"[^.\n]*\bgym\s+membership\b[^.\n]*")

    evidence = {
        "benefit": [{"quote": benefit_quote}] if benefit_quote else [],
        "amount": [{"quote": quote} for quote in amount_evidence],
        "currency": [{"quote": item[2]} for item in amounts] if currency else [],
        "reference": [{"quote": reference_quote}] if reference_quote and reference else [],
    }
    issues = []
    if not extracted_benefit:
        issues.append("BENEFIT_UNRESOLVED: The requested benefit could not be identified from the document.")

    if len(amounts) > 1:
        candidates = "; ".join(f"{item[1]} {item[0]}" for item in amounts)
        issues.append(f"AMOUNT_AMBIGUOUS: Multiple distinct amounts appear ({candidates}); the correct amount needs human confirmation.")

    elif not amounts:
        issues.append("AMOUNT_MISSING: No stated amount was found.")

    if not reference:
        issues.append("REFERENCE_MISSING: No reference was found.")

    # Policy and eligibility limitations must remain explicit. Only policy topics
    # supported by the corpus get a policy lookup; recognized gym membership does not.
    if benefit == "certification":
        issues.append("LIMITATION: An annual limit does not establish remaining balance, eligibility, or a payable amount; no claims history was supplied.")

    elif benefit == "home-office":
        issues.append("LIMITATION: An annual allowance does not establish remaining balance, eligibility, or a payable amount; no claims history was supplied.")

    elif benefit == "training":
        if re.search(r"not\s+(?:obtained|received|got)\s+manager approval|without\s+manager approval", text, re.IGNORECASE):
            issues.append("POLICY_REVIEW: The policy requires manager approval before external training is booked; the document says approval was not obtained and booking already occurred.")
        issues.append("LIMITATION: The policy does not establish whether this expense is payable.")

    elif benefit == "travel":
        issues.append("LIMITATION: The policy permits rail travel for approved business trips but does not establish whether this specific expense is payable.")

    else:
        issues.append("POLICY_GAP: No applicable policy benefit could be identified in the supplied corpus.")

    return ({"benefit": extracted_benefit, "amount": amount, "currency": currency, "reference": reference}, evidence, issues, benefit)

@app.get("/health")
def health():
    return {"status":"UP", "mode":"deterministic-offline", "policy_count":len(POLICIES)}

@app.post("/internal/answer", response_model=AnswerResponse)
def internal_answer(request: AnswerRequest):
    try:
        result = answer_question(request.question, request.tenant, request.role, request.as_of)
        log.info("policy_answer tenant=%s role=%s as_of=%s status=%s", request.tenant, request.role, request.as_of, result["status"])
        return result

    except Exception as exc:
        log.exception("policy_answer_failure")
        raise HTTPException(status_code=500, detail={"code":"POLICY_PROVIDER_FAILURE","message":"Policy analysis failed."}) from exc

@app.post("/internal/analyze")
async def analyze_document(tenant: str = Form(...), role: str = Form(...), as_of: str = Form(...), filename: str = Form(...), file: UploadFile = File(...)):
    try:
        parsed_date = date.fromisoformat(as_of)
        raw = await file.read()
        text = extract_text(filename, raw)
        if not text.strip():
            raise ValueError("UNREADABLE_FILE")
        extracted, evidence, issues, topic = extract_fields(text)

    # Detect a tenant mismatch in the document.
        tenant_match = re.search(r"\bI belong to\s+(Atlas|Boreal)\b", text, re.IGNORECASE,)
        if tenant_match:
            claimed_tenant = tenant_match.group(1)

            if claimed_tenant.casefold() != tenant.casefold():
                issues.append("IDENTITY_MISMATCH: The document claims tenant "
                                f"{claimed_tenant}, but the trusted caller belongs to "
                                f"{tenant}. Caller identity remains authoritative; "
                                "human review is required.")


        policy = answer_question(f"What is the applicable {topic or ''} policy?", tenant, role, parsed_date) if topic else None
        if policy and policy["status"] == "CONFLICT":
            issues.append("POLICY_CONFLICT: Applicable approved policy passages disagree; no precedence rule is supplied.")

        elif policy and policy["status"] == "INSUFFICIENT_EVIDENCE":
            issues.append("POLICY_GAP: No eligible approved policy evidence was found for this benefit, tenant, role, and date.")

        if extracted["amount"] and extracted["currency"]:
            pass
        log.info("document_analyzed filename=%s tenant=%s topic=%s", filename, tenant, topic or "unresolved")

        return {"extracted":extracted, "field_evidence":evidence, "policy":policy, "review_required":{"required":True,"issues":issues}}

    except ValueError as exc:
        code = str(exc) if str(exc) in {"EMPTY_FILE","UNREADABLE_FILE","UNSUPPORTED_FILE_TYPE"} else "INVALID_DATE_OR_INPUT"
        raise HTTPException(status_code=422, detail={"code":code,"message":"The document could not be processed."}) from exc

    except Exception as exc:
        log.exception("document_analysis_failure filename=%s", filename)
        raise HTTPException(status_code=500, detail={"code":"DOCUMENT_ANALYSIS_FAILURE","message":"Document analysis failed."}) from exc
