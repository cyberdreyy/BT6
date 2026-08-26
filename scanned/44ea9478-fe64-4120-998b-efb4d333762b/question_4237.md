# Q4237: double decoding of identifiers in auth.AuthenticateByToken

## Question
Is an identifier decoded twice between the authorization check and the lookup on the path through `AuthenticateByToken`, letting a holder of a restricted API access-key/secret pair authorize one object at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list and act on another?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: X-API-KEY and X-API-SECRET headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `X-API-KEY and X-API-SECRET headers` percent-encoded so the two stages resolve to different values.
- Invariant to test: the value authorized and the value used must be byte-identical
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the authorized identifier equals the identifier passed to the store
