# Q5580: response body injected into workflow input in callback.NewCallback

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests shape the response returned through `NewCallback` at the callback used to return a DON response to the originating gateway user so a workflow consumes attacker-controlled data as trusted input to an on-chain report?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `NewCallback`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: duplicate responses for one request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve `duplicate responses for one request` from a target the node fetches.
- Invariant to test: externally fetched data must be treated as untrusted at the consumption point
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test asserting fetched data cannot alter the reported value
