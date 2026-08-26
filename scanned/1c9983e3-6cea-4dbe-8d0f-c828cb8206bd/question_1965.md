# Q1965: session fixation in common.getChain

## Question
Does the session id observed on the path through `getChain` survive privilege changes at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes, letting an authenticated node user holding only the 'view' role pre-seed a session id that becomes privileged after the victim logs in?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: relayer network identifier (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Plant `relayer network identifier` and observe whether the id is regenerated on successful login.
- Invariant to test: a new session identifier must be issued on every successful authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the session id before and after login differ
