# Q4721: non-constant-time credential comparison in auth.AuthenticateExternalInitiator

## Question
Does the credential comparison reached by `AuthenticateExternalInitiator` from any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list short-circuit on the first differing byte, letting a holder of a restricted API access-key/secret pair recover a valid API/EI secret by measuring response timing across requests?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: X-API-KEY and X-API-SECRET headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send many requests varying `X-API-KEY and X-API-SECRET headers` one byte at a time and rank by latency.
- Invariant to test: all secret comparisons must be constant time over the full secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: benchmark/timing test over the comparison helper with prefix-matching secrets
