# Q5688: retry amplification per request in handler.armGraceDeadline

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests make one accepted request at HandleJSONRPCUserMessage on the confidential-relay gateway method cause repeated node work through the retry logic near `armGraceDeadline`, multiplying DON execution per unit of entitlement?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `armGraceDeadline`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: the relay request payload and method (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `relay request payload and method` that never reaches a terminal state.
- Invariant to test: retries must be bounded per request and counted against the caller's quota
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test counting node messages produced by one user request
