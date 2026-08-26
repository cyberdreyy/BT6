# Q3017: expiry check allows stale entries in handler.Callback

## Question
Does the expiry logic in `Callback` at the gateway handler interface boundary every public user request passes through keep serving a stale entry (inverted comparison, missing zero-value handling), letting any internet client with an arbitrary externally-owned key sending signed gateway requests pin an outdated result?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `Callback`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: callback correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `callback correlation fields` around the expiry boundary.
- Invariant to test: expired entries must never be served
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test at expiry boundaries
