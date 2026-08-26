# Q2256: stale role after change in common.getChain

## Question
Does a session or token validated through `getChain` keep its old role at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes after the role was downgraded or the user deleted, letting an authenticated node user holding only the 'view' role act with revoked privileges?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: chain id formatting (leading zeros, alternate base) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Continue sending `chain id formatting (leading zeros, alternate base)` on the existing session after the change.
- Invariant to test: role and existence must be re-read from the store on every request
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test downgrading a role mid-session and asserting the next request is rejected
