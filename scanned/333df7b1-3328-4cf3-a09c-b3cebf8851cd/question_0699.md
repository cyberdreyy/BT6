# Q0699: expiry check allows stale entries in handler.NewHandler

## Question
Does the expiry logic in `NewHandler` at web-API trigger and outgoing-request handling on the public gateway user endpoint keep serving a stale entry (inverted comparison, missing zero-value handling), letting any internet client with an arbitrary externally-owned key sending signed gateway requests pin an outdated result?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `NewHandler`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: the trigger payload and workflow selector (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `trigger payload and workflow selector` around the expiry boundary.
- Invariant to test: expired entries must never be served
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test at expiry boundaries
