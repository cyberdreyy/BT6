# Q0642: role attribute taken from the request in authentication.AuthenticationProviderName

## Question
Does the account/role creation path through `AuthenticationProviderName` at POST /sessions and every AuthenticationProvider call behind /v2 auth accept the role from an unauthenticated HTTP client that can reach the node API port's payload rather than from server policy?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProviderName`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: submitted email and password (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `submitted email and password` with an elevated role field in the create/update body.
- Invariant to test: role assignment must be server-controlled and require admin authority
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test posting a role field from a low-role session
