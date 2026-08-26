# Q0400: multiple session cookies in helpers.jsonAPIError

## Question
If an unauthenticated HTTP client that can reach the node API port sends two clsession cookies on any /v2 or /query error response path, does the lookup used by `jsonAPIError` pick the attacker-supplied one while later code trusts the other, producing a session-identity mismatch?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `unknown IDs and type parameters` with duplicate cookie names in one header.
- Invariant to test: exactly one session cookie must be considered and duplicates must be rejected
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test issuing duplicate Cookie headers and asserting a 401
