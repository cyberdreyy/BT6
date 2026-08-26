# Q4425: index route serves privileged payload in helpers.addForbiddenErrorHeaders

## Question
Can an unauthenticated HTTP client that can reach the node API port obtain configuration, feature flags or identity data embedded by `addForbiddenErrorHeaders` into the index/asset response at any /v2 or /query error response path without authenticating?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: inputs that force an error branch (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `inputs that force an error branch` anonymously and inspect the served document.
- Invariant to test: unauthenticated responses must contain no node configuration or identity data
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test fetching index/static routes anonymously and asserting a fixed payload
