from datetime import date
from app.main import answer_question, eligible_policies, extract_fields, extract_text, is_safe_policy, POLICIES


def test_certification_current_policy_has_verbatim_citation():
    result = answer_question("What is my annual certification reimbursement limit?", "Atlas", "employee", date(2026, 9, 21))
    assert result["status"] == "ANSWERED"
    assert result["answer"] == "The annual certification reimbursement limit is INR 25000."
    assert result["citations"] == [{"chunk_id":"atlas-cert-current", "quote":"The annual certification reimbursement limit for employees is INR 25000."}]


def test_effective_interval_excludes_end_date_and_selects_future_policy():
    before = answer_question("certification limit", "Atlas", "employee", date(2026, 6, 1))
    future = answer_question("certification limit", "Atlas", "employee", date(2027, 1, 1))
    assert before["citations"][0]["chunk_id"] == "atlas-cert-current"
    assert future["citations"][0]["chunk_id"] == "atlas-cert-future"


def test_caller_tenant_and_role_scope():
    employee = answer_question("certification limit", "Atlas", "employee", date(2026, 9, 21))
    contractor = answer_question("certification limit", "Atlas", "contractor", date(2026, 9, 21))
    boreal = answer_question("certification limit", "Boreal", "employee", date(2026, 9, 21))
    assert employee["citations"][0]["chunk_id"] == "atlas-cert-current"
    assert contractor["citations"][0]["chunk_id"] == "atlas-cert-contractor"
    assert boreal["citations"][0]["chunk_id"] == "boreal-cert-current"


def test_home_office_conflict_does_not_invent_precedence():
    result = answer_question("What is my home office allowance?", "Atlas", "employee", date(2026, 9, 21))
    assert result["status"] == "CONFLICT" and result["answer"] is None
    assert {c["chunk_id"] for c in result["citations"]} == {"atlas-home-office-a", "atlas-home-office-b"}


def test_unknown_benefit_is_insufficient_and_injection_never_eligible():
    result = answer_question("What wellness benefit am I entitled to?", "Atlas", "employee", date(2026, 9, 21))
    assert result == {"status":"INSUFFICIENT_EVIDENCE", "answer":None, "citations":[]}
    assert all(is_safe_policy(p) for p in eligible_policies("Atlas", "employee", date(2026, 9, 21)))
    assert any(p["id"] == "atlas-injection-example" and not is_safe_policy(p) for p in POLICIES)


def test_ambiguous_amount_remains_null_and_reviewable():
    text = "Reference: CERT-303\nCertification reimbursement request. The invoice says INR 22000. My reimbursement form says INR 28000."
    extracted, evidence, issues, topic = extract_fields(text)
    assert topic == "certification"
    assert extracted["amount"] is None and extracted["currency"] == "INR"
    assert len(evidence["amount"]) == 2
    assert any(x.startswith("AMOUNT_AMBIGUOUS") and "22000" in x and "28000" in x for x in issues)


def test_empty_and_utf8_txt():
    try:
        extract_text("empty.txt", b"")
        assert False
    except ValueError as exc:
        assert str(exc) == "EMPTY_FILE"
    assert extract_text("note.txt", "hello".encode()) == "hello"


def test_model_provider_timeout_unavailable_and_malformed_output_are_failures():
    from app.model import generate_once, ModelTimeout, ModelUnavailable, MalformedModelOutput
    evidence = [{"id":"atlas-cert-current", "text":"The annual certification reimbursement limit for employees is INR 25000."}]
    class TimeoutProvider:
        calls = 0
        def generate(self, question, topic, evidence):
            self.calls += 1
            raise TimeoutError()
    class UnavailableProvider:
        calls = 0
        def generate(self, question, topic, evidence):
            self.calls += 1
            raise ConnectionError()
    class MalformedProvider:
        calls = 0
        def generate(self, question, topic, evidence):
            self.calls += 1
            return '{not-json'
    for provider, expected in [(TimeoutProvider(), ModelTimeout), (UnavailableProvider(), ModelUnavailable), (MalformedProvider(), MalformedModelOutput)]:
        try:
            generate_once(provider, "certification limit", "certification", evidence)
            assert False, "expected provider failure"
        except expected:
            assert provider.calls == 1  # no automatic retries


def test_policy_order_and_duplicate_records_do_not_change_outcome(monkeypatch):
    import app.main as policy_module
    original = list(policy_module.POLICIES)
    expected = answer_question("certification limit", "Atlas", "employee", date(2026, 9, 21))
    current = next(p for p in original if p["id"] == "atlas-cert-current")
    
    monkeypatch.setattr(policy_module, "POLICIES", list(reversed(original)) + [dict(current)])
    actual = answer_question("certification limit", "Atlas", "employee", date(2026, 9, 21))
    assert actual["status"] == expected["status"]
    assert actual["answer"] == expected["answer"]
    assert actual["citations"][0] == expected["citations"][0]



def test_provider_failures_propagate_through_answer_question(monkeypatch):
    import pytest
    import app.main as policy_module

    class FailingProvider:
        def __init__(self, failure):
            self.failure = failure
            self.calls = 0

        def generate(self, question, topic, evidence):
            self.calls += 1

            if self.failure == "timeout":
                raise TimeoutError()

            if self.failure == "unavailable":
                raise ConnectionError()

            return "{not-json"

    cases = [
        ("timeout", "ModelTimeout"),
        ("unavailable", "ModelUnavailable"),
        ("malformed", "MalformedModelOutput"),
    ]

    for failure, expected_error in cases:
        provider = FailingProvider(failure)
        monkeypatch.setattr(policy_module, "MODEL", provider)

        with pytest.raises(RuntimeError, match=expected_error):
            policy_module.answer_question(
                "What is my certification limit?",
                "Atlas",
                "employee",
                date(2026, 9, 21),
            )

        assert provider.calls == 1  # No automatic retry
