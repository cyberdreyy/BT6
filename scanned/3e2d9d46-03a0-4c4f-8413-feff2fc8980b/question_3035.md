# Q3035: static asset path traversal in helpers.addForbiddenErrorHeaders

## Question
Can an unauthenticated HTTP client that can reach the node API port escape the embedded asset root through `addForbiddenErrorHeaders` at any /v2 or /query error response path and read node files such as TLS keys, keystore files or the config secrets file?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `unknown IDs and type parameters` containing encoded dot-segments, backslashes or unicode separators.
- Invariant to test: asset serving must be confined to the embedded filesystem regardless of input encoding
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the path resolver with traversal payloads asserting no host file is opened
