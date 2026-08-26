# Q3021: expiry check allows stale entries in workflow_metadata_handler.Authorize

## Question
Does the expiry logic in `Authorize` at the workflow metadata/authorization lookup consulted for every user trigger request keep serving a stale entry (inverted comparison, missing zero-value handling), letting any internet client with an arbitrary externally-owned key sending signed gateway requests pin an outdated result?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `Authorize`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: authorization key material presented (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `authorization key material presented` around the expiry boundary.
- Invariant to test: expired entries must never be served
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test at expiry boundaries
