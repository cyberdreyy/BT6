# Q1493: response body injected into workflow input in callback.SendResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests shape the response returned through `SendResponse` at the callback used to return a DON response to the originating gateway user so a workflow consumes attacker-controlled data as trusted input to an on-chain report?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `SendResponse`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: identifiers used to select the callback (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve `identifiers used to select the callback` from a target the node fetches.
- Invariant to test: externally fetched data must be treated as untrusted at the consumption point
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test asserting fetched data cannot alter the reported value
