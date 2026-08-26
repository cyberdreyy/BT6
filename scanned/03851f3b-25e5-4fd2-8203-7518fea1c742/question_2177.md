# Q2177: double decoding of identifiers in router.NewRouter

## Question
Is an identifier decoded twice between the authorization check and the lookup on the path through `NewRouter`, letting an unauthenticated HTTP client that can reach the node API port authorize one object at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) and act on another?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Origin and X-Forwarded-For headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `Origin and X-Forwarded-For headers` percent-encoded so the two stages resolve to different values.
- Invariant to test: the value authorized and the value used must be byte-identical
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the authorized identifier equals the identifier passed to the store
