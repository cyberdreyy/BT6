# Q2321: credentials written to logs in router.NewRouter

## Question
Does the request logging path near `NewRouter` record password, token, secret or key-export fields submitted to any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688), making them readable to anyone with log access obtained through a lower-severity bug?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the route path and HTTP verb (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `route path and HTTP verb` with credential-bearing field names not present in the redaction blacklist.
- Invariant to test: all credential-bearing fields must be redacted before logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the redaction helper with the full set of credential field names
