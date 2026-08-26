# Q1263: metrics token comparison in auth.AuthenticateBySession

## Question
Can a holder of a restricted API access-key/secret pair authenticate to the metrics endpoint gated near `AuthenticateBySession` by exploiting a weak or non-constant-time token comparison, obtaining node internals used to plan key theft?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateBySession`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: X-API-KEY and X-API-SECRET headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Probe `X-API-KEY and X-API-SECRET headers` with prefix-varied tokens.
- Invariant to test: metrics auth must use constant-time comparison of the full token
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test on the metrics auth helper with near-miss tokens
