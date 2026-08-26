# Q3036: static asset path traversal in api.paginationLink

## Question
Can an authenticated node user holding only the 'view' role escape the embedded asset root through `paginationLink` at page/size query parameters on /v2 index endpoints and read node files such as TLS keys, keystore files or the config secrets file?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: JSON:API document fields in the request body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `JSON:API document fields in the request body` containing encoded dot-segments, backslashes or unicode separators.
- Invariant to test: asset serving must be confined to the embedded filesystem regardless of input encoding
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the path resolver with traversal payloads asserting no host file is opened
