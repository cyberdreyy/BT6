# Q1424: state-changing request without origin binding in common.getChain

## Question
Can a page loaded by a logged-in operator cause an authenticated node user holding only the 'view' role's chosen state change at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes through `getChain` because the session cookie alone authorizes the mutation?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: evmChainID query/body value (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Auto-submit `evmChainID query/body value` from an attacker page targeting a key-export or transfer route.
- Invariant to test: state-changing requests must require a non-cookie credential or origin binding
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test issuing a cross-site style request with only a session cookie
