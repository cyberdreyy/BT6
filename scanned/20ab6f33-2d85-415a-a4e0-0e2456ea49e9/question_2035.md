# Q2035: path normalization mismatch in helpers.jsonAPIError

## Question
Can an unauthenticated HTTP client that can reach the node API port reach a protected handler through `jsonAPIError` using a path variant (trailing slash, double slash, encoded segment) that the router matches but the role middleware does not?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `unknown IDs and type parameters` in normalized and non-normalized forms.
- Invariant to test: route matching and middleware application must operate on the same normalized path
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test comparing handler execution across path variants
