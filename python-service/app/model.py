"""
The assessment can be run without external model credentials. A future hosted adapter
must implement generate() and must preserve the same one-attempt/error contract.
"""
from __future__ import annotations
import json
import re
from typing import Protocol

class ModelTimeout(Exception): pass
class ModelUnavailable(Exception): pass
class MalformedModelOutput(Exception): pass

class ModelProvider(Protocol):
    def generate(self, question: str, topic: str, evidence: list[dict]) -> str: ...

class OfflineModel:

    """Predictable local double; it only sees already-filtered evidence."""

    def generate(self, question: str, topic: str, evidence: list[dict]) -> str:
        ids = [p["id"] for p in evidence]

        if not evidence:
            return json.dumps({"status":"INSUFFICIENT_EVIDENCE", "answer":None, "citation_ids":[]})
        
        if topic in ("certification", "home-office"):
            amounts = set()
            for p in evidence:
                m = re.search(r"\bINR\s+[0-9,]+\b", p["text"], re.I)
                if m: amounts.add(m.group(0))

            if len(amounts) > 1:
                return json.dumps({"status":"CONFLICT", "answer":None, "citation_ids":ids})
            
            if amounts:
                label = "annual certification reimbursement limit" if topic == "certification" else "annual home-office allowance"
                return json.dumps({"status":"ANSWERED", "answer":f"The {label} is {next(iter(amounts))}.", "citation_ids":ids})
            
        if len(evidence) > 1:
            return json.dumps({"status":"CONFLICT", "answer":None, "citation_ids":ids})
        return json.dumps({"status":"ANSWERED", "answer":evidence[0]["text"], "citation_ids":ids})

def generate_once(provider: ModelProvider, question: str, topic: str, evidence: list[dict]) -> dict:
    """Exactly one generation attempt. Provider errors and invalid output are not policy gaps."""
    try:
        raw = provider.generate(question, topic, evidence)

    except (ModelTimeout, ModelUnavailable):
        raise

    except TimeoutError as exc:
        raise ModelTimeout("Model generation timed out") from exc
    
    except ConnectionError as exc:
        raise ModelUnavailable("Model provider unavailable") from exc
    
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("status") not in {"ANSWERED", "INSUFFICIENT_EVIDENCE", "CONFLICT"}:
            raise ValueError("bad status")
        
        if result.get("status") == "ANSWERED" and (not isinstance(result.get("answer"), str) or not result["answer"].strip()):
            raise ValueError("answer missing")
        
        if result.get("status") != "ANSWERED" and result.get("answer") is not None:
            raise ValueError("unsupported answer")
        
        if not isinstance(result.get("citation_ids"), list) or any(not isinstance(x, str) for x in result["citation_ids"]):
            raise ValueError("citation ids malformed")
        
        allowed = {p["id"] for p in evidence}

        if any(cid not in allowed for cid in result["citation_ids"]):
            raise ValueError("model cited ineligible evidence")
        
        if result["status"] == "INSUFFICIENT_EVIDENCE" and result["citation_ids"]:
            raise ValueError("insufficient evidence must have no citations")
        
        if result["status"] in {"ANSWERED", "CONFLICT"} and not result["citation_ids"]:
            raise ValueError("supported outcomes require citations")
        
        return result
    
    except Exception as exc:
        raise MalformedModelOutput("Model output did not match the response contract") from exc
