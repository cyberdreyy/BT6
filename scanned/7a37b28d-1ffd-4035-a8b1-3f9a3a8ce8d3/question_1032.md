# Q1032: chain selector reaches unintended relayer in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port supply a chain identifier through `FindSessionCookie` at the Cookie header on any authenticated /v2 route that resolves to a relayer/keystore other than the one authorization was evaluated against?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie name casing and attributes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `cookie name casing and attributes` with alternate encodings of the chain id (leading zeros, whitespace, different base).
- Invariant to test: the chain resolved for execution must be the exact chain authorized for the request
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over getChain with equivalent-but-different chain id strings
