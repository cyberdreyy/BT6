# Q2953: cache poisoning of another user's result in handler.Callback

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `Callback` at the gateway handler interface boundary every public user request passes through so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `Callback`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: the method and payload of the user request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `method and payload of the user request`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
