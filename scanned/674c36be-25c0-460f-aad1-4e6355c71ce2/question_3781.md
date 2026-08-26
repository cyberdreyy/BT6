# Q3781: retry amplification per request in handler.sendHTTPMessageToClient

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests make one accepted request at web-API trigger and outgoing-request handling on the public gateway user endpoint cause repeated node work through the retry logic near `sendHTTPMessageToClient`, multiplying DON execution per unit of entitlement?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `sendHTTPMessageToClient`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: the trigger payload and workflow selector (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `trigger payload and workflow selector` that never reaches a terminal state.
- Invariant to test: retries must be bounded per request and counted against the caller's quota
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test counting node messages produced by one user request
