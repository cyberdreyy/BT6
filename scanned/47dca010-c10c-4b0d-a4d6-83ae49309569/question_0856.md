# Q0856: duplicate response overwrites the result in webapi.Validate

## Question
Can a second response accepted by `Validate` at the web-API capability handler config validation and request path from the public gateway endpoint overwrite an already-delivered result so any internet client with an arbitrary externally-owned key sending signed gateway requests changes what a workflow consumes?

## Target
- File/function: [core/services/gateway/handlers/capabilities/webapi.go](core/services/gateway/handlers/capabilities/webapi.go) -> `Validate`
- Entrypoint: the web-API capability handler config validation and request path from the public gateway endpoint
- Attacker controls: workflow identifiers in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force duplicates via `workflow identifiers in the request`.
- Invariant to test: response processing must be single-shot per request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test sending duplicate responses and asserting the first wins
