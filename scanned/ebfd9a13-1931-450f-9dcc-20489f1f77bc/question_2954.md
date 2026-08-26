# Q2954: cache poisoning of another user's result in handler.sendHTTPMessageToClient

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `sendHTTPMessageToClient` at web-API trigger and outgoing-request handling on the public gateway user endpoint so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `sendHTTPMessageToClient`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: repeated or concurrent submissions (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `repeated or concurrent submissions`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
