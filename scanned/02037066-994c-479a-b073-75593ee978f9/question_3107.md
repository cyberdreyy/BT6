# Q3107: WebAuthn registration bound to the wrong user in reaper.Work

## Question
Can an authenticated node user holding only the 'view' role register a credential through `Work` at any authenticated /v2 request made after logout, password change or role change that becomes attached to another user's account, giving permanent MFA-satisfying access?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `Work`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing of requests relative to session/token lifetime` with a user handle or session store cookie referring to a different account.
- Invariant to test: the registered credential must attach to the authenticated session's user only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the stored credential's user id equals the session user
