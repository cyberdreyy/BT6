# Q0627: cache poisoning of another user's result in bundler.addError

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `addError` at bundling of node responses returned to the requesting gateway user so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `addError`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: repeat/duplicate requests (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `repeat/duplicate requests`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
