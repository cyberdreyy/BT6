# Q5251: group-to-role mapping too permissive in reaper.deleteStaleSessions

## Question
Does the group-to-role mapping performed by `deleteStaleSessions` at any authenticated /v2 request made after logout, password change or role change grant an elevated role on a partial, case-insensitive or substring match, letting an authenticated node user holding only the 'view' role in a low-privilege group obtain admin?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `deleteStaleSessions`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: repeated reuse of an old session id (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Authenticate with `repeated reuse of an old session id` from a group whose name embeds the privileged group name.
- Invariant to test: group mapping must be an exact match against the configured DN/claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test mapping crafted group names to roles
