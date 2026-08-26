# Q2659: token compared without constant time in reaper.Work

## Question
Does the secret comparison used by `Work` at any authenticated /v2 request made after logout, password change or role change leak byte position through timing or early return, letting an authenticated node user holding only the 'view' role recover an admin API secret?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `Work`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed requests varying `repeated reuse of an old session id`.
- Invariant to test: all token/secret comparisons must be constant time
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing test over the comparison helper with prefix-matched secrets
