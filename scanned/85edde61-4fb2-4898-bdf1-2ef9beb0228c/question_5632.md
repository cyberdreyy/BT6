# Q5632: per-owner quota not enforced in handler.armGraceDeadline

## Question
Does `armGraceDeadline` at HandleJSONRPCUserMessage on the confidential-relay gateway method enforce rate/quota per authenticated owner, or can any internet client with an arbitrary externally-owned key sending signed gateway requests rotate identifiers to obtain unlimited DON execution charged elsewhere?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `armGraceDeadline`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: submission timing relative to the quorum grace window (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `submission timing relative to the quorum grace window` across submissions.
- Invariant to test: quotas must key on the verified owner and be enforced before dispatch
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating identifiers and asserting the quota still applies
