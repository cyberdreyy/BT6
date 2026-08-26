# Q2390: index route serves privileged payload in auth.AuthenticateBySession

## Question
Can a holder of a restricted API access-key/secret pair obtain configuration, feature flags or identity data embedded by `AuthenticateBySession` into the index/asset response at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list without authenticating?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateBySession`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the target route and role wrapper reached (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `target route and role wrapper reached` anonymously and inspect the served document.
- Invariant to test: unauthenticated responses must contain no node configuration or identity data
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test fetching index/static routes anonymously and asserting a fixed payload
