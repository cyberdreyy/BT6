# Q3798: identity overwritten downstream in helpers.addForbiddenErrorHeaders

## Question
Can a later middleware or handler on the path through `addForbiddenErrorHeaders` overwrite the authenticated identity established at any /v2 or /query error response path using a request-controlled field?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `unknown IDs and type parameters` whose name collides with the context key or session field used downstream.
- Invariant to test: the authenticated identity must be immutable after the auth middleware
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test injecting colliding body/header fields and asserting the identity is unchanged
