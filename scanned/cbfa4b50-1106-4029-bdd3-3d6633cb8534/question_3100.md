# Q3100: content-encoding negotiation file selection in api.paginationLink

## Question
Can an authenticated node user holding only the 'view' role steer the file chosen by `paginationLink` via encoding negotiation on page/size query parameters on /v2 index endpoints so a file outside the intended asset set is served?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Combine `page and size query values` with crafted Accept-Encoding values that make the server append a suffix to an attacker-chosen path.
- Invariant to test: negotiation may only select among pre-registered asset variants
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test over findBestFile/negotiateContentEncoding with hostile paths and encodings
