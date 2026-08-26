# Q2527: password verification skipped on missing user in session.GenerateAuthToken

## Question
Does `GenerateAuthToken` skip or short-circuit hash verification when the user row is absent or has an empty password hash, letting an unauthenticated HTTP client that can reach the node API port authenticate at POST /sessions (session creation) and API-token authentication against a non-existent or partially provisioned account?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `GenerateAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: email/password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `email/password fields` for an unknown or externally-managed account.
- Invariant to test: authentication must fail closed and always perform a full hash comparison
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test asserting a constant-time failure for unknown users and empty hashes
