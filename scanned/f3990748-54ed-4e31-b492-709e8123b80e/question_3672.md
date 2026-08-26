# Q3672: empty or absent credential accepted in helpers.addForbiddenErrorHeaders

## Question
Does `addForbiddenErrorHeaders` treat an empty access key, empty secret or empty session id presented at any /v2 or /query error response path as a match against an unset/zero stored value, authenticating an unauthenticated HTTP client that can reach the node API port as a real identity?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: inputs that force an error branch (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `inputs that force an error branch` with empty or omitted credential fields.
- Invariant to test: empty credentials must always fail authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/absent credential fields asserting 401
