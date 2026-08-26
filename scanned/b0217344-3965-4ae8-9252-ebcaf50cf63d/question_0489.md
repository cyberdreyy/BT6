# Q0489: password change without old-password proof in reaper.NewSessionReaper

## Question
Can an authenticated node user holding only the 'view' role change the password (or set a new one) through the path reaching `NewSessionReaper` at any authenticated /v2 request made after logout, password change or role change without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing of requests relative to session/token lifetime` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
