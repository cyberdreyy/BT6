# Q3609: state-changing request without origin binding in helpers.addForbiddenErrorHeaders

## Question
Can a page loaded by a logged-in operator cause an unauthenticated HTTP client that can reach the node API port's chosen state change at any /v2 or /query error response path through `addForbiddenErrorHeaders` because the session cookie alone authorizes the mutation?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Auto-submit `unknown IDs and type parameters` from an attacker page targeting a key-export or transfer route.
- Invariant to test: state-changing requests must require a non-cookie credential or origin binding
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test issuing a cross-site style request with only a session cookie
