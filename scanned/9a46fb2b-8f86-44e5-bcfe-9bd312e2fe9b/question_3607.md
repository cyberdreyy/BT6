# Q3607: state-changing request without origin binding in auth.AuthenticateByToken

## Question
Can a page loaded by a logged-in operator cause a holder of a restricted API access-key/secret pair's chosen state change at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list through `AuthenticateByToken` because the session cookie alone authorizes the mutation?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: external-initiator accessKey/secret headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Auto-submit `external-initiator accessKey/secret headers` from an attacker page targeting a key-export or transfer route.
- Invariant to test: state-changing requests must require a non-cookie credential or origin binding
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test issuing a cross-site style request with only a session cookie
