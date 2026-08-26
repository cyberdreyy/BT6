# Q0791: content-encoding negotiation file selection in router.NewRouter

## Question
Can an unauthenticated HTTP client that can reach the node API port steer the file chosen by `NewRouter` via encoding negotiation on any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) so a file outside the intended asset set is served?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the route path and HTTP verb (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Combine `route path and HTTP verb` with crafted Accept-Encoding values that make the server append a suffix to an attacker-chosen path.
- Invariant to test: negotiation may only select among pre-registered asset variants
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test over findBestFile/negotiateContentEncoding with hostile paths and encodings
