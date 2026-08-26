# Q3031: static asset path traversal in router.graphqlHandler

## Question
Can an unauthenticated HTTP client that can reach the node API port escape the embedded asset root through `graphqlHandler` at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) and read node files such as TLS keys, keystore files or the config secrets file?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `graphqlHandler`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Authorization / X-API-KEY headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `Authorization / X-API-KEY headers` containing encoded dot-segments, backslashes or unicode separators.
- Invariant to test: asset serving must be confined to the embedded filesystem regardless of input encoding
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the path resolver with traversal payloads asserting no host file is opened
