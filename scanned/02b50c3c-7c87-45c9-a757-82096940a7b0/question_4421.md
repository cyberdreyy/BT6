# Q4421: index route serves privileged payload in router.graphqlHandler

## Question
Can an unauthenticated HTTP client that can reach the node API port obtain configuration, feature flags or identity data embedded by `graphqlHandler` into the index/asset response at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) without authenticating?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `graphqlHandler`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Origin and X-Forwarded-For headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `Origin and X-Forwarded-For headers` anonymously and inspect the served document.
- Invariant to test: unauthenticated responses must contain no node configuration or identity data
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test fetching index/static routes anonymously and asserting a fixed payload
