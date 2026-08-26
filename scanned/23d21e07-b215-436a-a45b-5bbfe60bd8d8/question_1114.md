# Q1114: unauthenticated bind treated as success in authentication.AuthenticationProviderName

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate at POST /sessions and every AuthenticationProvider call behind /v2 auth through `AuthenticationProviderName` by submitting an empty password so the directory performs an unauthenticated bind that the code reads as success?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProviderName`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: session id presented (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `session id presented` with an empty or whitespace password.
- Invariant to test: empty-password binds must be rejected before contacting the directory
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/space passwords asserting rejection
