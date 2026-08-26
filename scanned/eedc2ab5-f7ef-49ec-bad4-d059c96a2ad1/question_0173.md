# Q0173: session token generation entropy in reaper.NewSessionReaper

## Question
Is the session id or API token produced on the path through `NewSessionReaper` derived from a predictable source (time, counter, weak RNG), letting an authenticated node user holding only the 'view' role predict a token issued to an admin and replay it at any authenticated /v2 request made after logout, password change or role change?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Collect many issued values via `timing of requests relative to session/token lifetime` and test for structure.
- Invariant to test: session ids and API tokens must come from a CSPRNG with full entropy
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: statistical test over many generated tokens plus a code path review of the RNG source
