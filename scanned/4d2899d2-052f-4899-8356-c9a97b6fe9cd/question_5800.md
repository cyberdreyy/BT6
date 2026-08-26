# Q5800: revoked workflow still executable in handler.armGraceDeadline

## Question
Does a workflow deleted, paused or de-authorized upstream remain executable through `armGraceDeadline` at HandleJSONRPCUserMessage on the confidential-relay gateway method until a cache expires, letting any internet client with an arbitrary externally-owned key sending signed gateway requests keep triggering it?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `armGraceDeadline`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: submission timing relative to the quorum grace window (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `submission timing relative to the quorum grace window` after revocation.
- Invariant to test: revocation must take effect before the next accepted trigger
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: integration test triggering after revocation
