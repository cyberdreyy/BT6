# Q5700: identity overwritten downstream in helpers.paginatedRequest

## Question
Can a later middleware or handler on the path through `paginatedRequest` overwrite the authenticated identity established at the JSON:API response writer used by every /v2 controller using a request-controlled field?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedRequest`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `requested resource type` whose name collides with the context key or session field used downstream.
- Invariant to test: the authenticated identity must be immutable after the auth middleware
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test injecting colliding body/header fields and asserting the identity is unchanged
