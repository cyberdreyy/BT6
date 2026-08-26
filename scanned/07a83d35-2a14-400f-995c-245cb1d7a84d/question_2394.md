# Q2394: index route serves privileged payload in api.ParsePaginatedRequest

## Question
Can an authenticated node user holding only the 'view' role obtain configuration, feature flags or identity data embedded by `ParsePaginatedRequest` into the index/asset response at page/size query parameters on /v2 index endpoints without authenticating?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `ParsePaginatedRequest`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: Link header follow-up requests (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `Link header follow-up requests` anonymously and inspect the served document.
- Invariant to test: unauthenticated responses must contain no node configuration or identity data
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test fetching index/static routes anonymously and asserting a fixed payload
