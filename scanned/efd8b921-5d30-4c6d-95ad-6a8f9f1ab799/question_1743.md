# Q1743: user enumeration then targeted attack in reaper.NewSessionReaper

## Question
Do responses from `NewSessionReaper` at any authenticated /v2 request made after logout, password change or role change distinguish unknown accounts from wrong passwords precisely enough for an authenticated node user holding only the 'view' role to enumerate operator accounts before credential attacks?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare status/body/timing for `timing of requests relative to session/token lifetime` across known and unknown accounts.
- Invariant to test: authentication failures must be uniform in content and timing
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test comparing responses for known/unknown accounts
