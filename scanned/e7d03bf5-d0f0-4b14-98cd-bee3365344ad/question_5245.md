# Q5245: chain selector reaches unintended relayer in helpers.paginatedRequest

## Question
Can an authenticated node user holding only the 'view' role supply a chain identifier through `paginatedRequest` at the JSON:API response writer used by every /v2 controller that resolves to a relayer/keystore other than the one authorization was evaluated against?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedRequest`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: pagination parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `pagination parameters` with alternate encodings of the chain id (leading zeros, whitespace, different base).
- Invariant to test: the chain resolved for execution must be the exact chain authorized for the request
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over getChain with equivalent-but-different chain id strings
