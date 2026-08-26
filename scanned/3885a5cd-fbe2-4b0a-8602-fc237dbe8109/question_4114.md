# Q4114: path normalization mismatch in api.paginationLink

## Question
Can an authenticated node user holding only the 'view' role reach a protected handler through `paginationLink` using a path variant (trailing slash, double slash, encoded segment) that the router matches but the role middleware does not?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: Link header follow-up requests (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `Link header follow-up requests` in normalized and non-normalized forms.
- Invariant to test: route matching and middleware application must operate on the same normalized path
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test comparing handler execution across path variants
