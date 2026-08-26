# Q3733: external-initiator credential over-scoped in auth.AuthenticateByToken

## Question
Can a holder of a restricted API access-key/secret pair use an external-initiator credential accepted by `AuthenticateByToken` on any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: X-API-KEY and X-API-SECRET headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `X-API-KEY and X-API-SECRET headers` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
