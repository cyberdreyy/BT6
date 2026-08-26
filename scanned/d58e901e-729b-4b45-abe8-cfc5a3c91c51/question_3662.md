# Q3662: response body injected into workflow input in bundler.setSignedResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests shape the response returned through `setSignedResponse` at bundling of node responses returned to the requesting gateway user so a workflow consumes attacker-controlled data as trusted input to an on-chain report?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `setSignedResponse`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: fields echoed back into the bundle (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve `fields echoed back into the bundle` from a target the node fetches.
- Invariant to test: externally fetched data must be treated as untrusted at the consumption point
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test asserting fetched data cannot alter the reported value
