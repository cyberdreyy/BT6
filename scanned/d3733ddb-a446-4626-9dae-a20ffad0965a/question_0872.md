# Q0872: secret disclosure through error body in auth.AuthenticateBySession

## Question
Does an error path reached from any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list through `AuthenticateBySession` serialize internal values (config secrets, DB DSN, key material, tokens) into the JSON:API error returned to a holder of a restricted API access-key/secret pair?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateBySession`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the target route and role wrapper reached (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch with `target route and role wrapper reached` and inspect the returned detail string.
- Invariant to test: error responses must contain no server-side secret or connection string
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies match an allowlist of messages
