# Decision note

**Consequential choice:** Spring Boot owns public caller identity and request/manifest validation; Python owns document parsing, policy retrieval and generation. Caller tenant/role are resolved by a server-side allowlist and passed internally. This avoids letting an uploaded document select its own tenant and follows the assessment's explicit service boundary.

**Alternative rejected:** A single Python public API would be simpler but would violate the required Spring Boot public API boundary. A hosted LLM was not selected as the default because the assignment requires deterministic offline operation without a personal key, and the supplied corpus is small enough to retrieve transparently.

**Main limitation:** benefit identification and retrieval use deterministic keyword/topic rules, and PDFs are text extraction only. The offline model is a predictable test double, not evidence that a production LLM would meet quality or security requirements.

**Unfinished work:** Final submission review, publication of the public GitHub repository, and verification of the final commit SHA. AI assistance was used to draft and review code, tests, and documentation. The candidate should understand and be able to explain every component before submission.