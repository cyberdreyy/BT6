# Q4427: index route serves privileged payload in helpers.paginatedResponse

## Question
Can an authenticated node user holding only the 'view' role obtain configuration, feature flags or identity data embedded by `paginatedResponse` into the index/asset response at the JSON:API response writer used by every /v2 controller without authenticating?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: inputs that select the error branch (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `inputs that select the error branch` anonymously and inspect the served document.
- Invariant to test: unauthenticated responses must contain no node configuration or identity data
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test fetching index/static routes anonymously and asserting a fixed payload
