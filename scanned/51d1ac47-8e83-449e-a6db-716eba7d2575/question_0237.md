# Q0237: identifier normalization mismatch in message_util.ValidatedMessageFromResp

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit workflow identifiers at validated conversion between gateway messages, requests and responses whose normalization in `ValidatedMessageFromResp` differs from the form used for authorization or accounting, so one identity authorizes and another executes?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromResp`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: encoding variants of identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `encoding variants of identifiers` in mixed-case/0x-less/padded hex.
- Invariant to test: the canonical form must be computed once and reused for both decisions
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting one canonical form is used for auth and execution
