# Q5576: response body injected into workflow input in bundler.newBundleSummary

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests shape the response returned through `newBundleSummary` at bundling of node responses returned to the requesting gateway user so a workflow consumes attacker-controlled data as trusted input to an on-chain report?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `newBundleSummary`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: repeat/duplicate requests (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve `repeat/duplicate requests` from a target the node fetches.
- Invariant to test: externally fetched data must be treated as untrusted at the consumption point
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test asserting fetched data cannot alter the reported value
