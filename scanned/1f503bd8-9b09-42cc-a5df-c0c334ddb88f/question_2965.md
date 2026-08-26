# Q2965: cache poisoning of another user's result in callback.Wait

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `Wait` at the callback used to return a DON response to the originating gateway user so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `Wait`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: identifiers used to select the callback (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `identifiers used to select the callback`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
