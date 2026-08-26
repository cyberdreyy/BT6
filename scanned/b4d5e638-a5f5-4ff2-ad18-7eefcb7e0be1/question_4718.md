# Q4718: length/format validation gap in message_util.ValidatedResponseFromMessage

## Question
Does the field validation in `ValidatedResponseFromMessage` at validated conversion between gateway messages, requests and responses accept an over-long, truncated or non-hex identifier that later code slices or parses unchecked, letting any internet client with an arbitrary externally-owned key sending signed gateway requests address a different workflow?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedResponseFromMessage`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: response fields echoed from the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `response fields echoed from the request` at and beyond the documented length bounds.
- Invariant to test: every identifier must be length- and charset-validated before use
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at length boundaries for each identifier field
