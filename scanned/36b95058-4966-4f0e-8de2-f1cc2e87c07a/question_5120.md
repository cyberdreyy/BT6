# Q5120: duplicate response overwrites the result in bundler.newBundleSummary

## Question
Can a second response accepted by `newBundleSummary` at bundling of node responses returned to the requesting gateway user overwrite an already-delivered result so any internet client with an arbitrary externally-owned key sending signed gateway requests changes what a workflow consumes?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `newBundleSummary`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: fields echoed back into the bundle (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force duplicates via `fields echoed back into the bundle`.
- Invariant to test: response processing must be single-shot per request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test sending duplicate responses and asserting the first wins
