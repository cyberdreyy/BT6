# Q3292: chain selector reaches unintended relayer in api.paginationLink

## Question
Can an authenticated node user holding only the 'view' role supply a chain identifier through `paginationLink` at page/size query parameters on /v2 index endpoints that resolves to a relayer/keystore other than the one authorization was evaluated against?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `page and size query values` with alternate encodings of the chain id (leading zeros, whitespace, different base).
- Invariant to test: the chain resolved for execution must be the exact chain authorized for the request
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over getChain with equivalent-but-different chain id strings
