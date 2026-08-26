# Q0094: password verification skipped on missing user in reaper.NewSessionReaper

## Question
Does `NewSessionReaper` skip or short-circuit hash verification when the user row is absent or has an empty password hash, letting an authenticated node user holding only the 'view' role authenticate at any authenticated /v2 request made after logout, password change or role change against a non-existent or partially provisioned account?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeated reuse of an old session id` for an unknown or externally-managed account.
- Invariant to test: authentication must fail closed and always perform a full hash comparison
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test asserting a constant-time failure for unknown users and empty hashes
