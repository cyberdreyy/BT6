# Q3164: secret disclosure through error body in api.paginationLink

## Question
Does an error path reached from page/size query parameters on /v2 index endpoints through `paginationLink` serialize internal values (config secrets, DB DSN, key material, tokens) into the JSON:API error returned to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: Link header follow-up requests (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch with `Link header follow-up requests` and inspect the returned detail string.
- Invariant to test: error responses must contain no server-side secret or connection string
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies match an allowlist of messages
