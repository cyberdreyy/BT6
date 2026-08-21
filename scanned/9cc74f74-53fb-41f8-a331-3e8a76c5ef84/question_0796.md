# Q0796: session_update_action drives local state in logger.ts

## Question
_refreshSession applies set/clear/ignore purely from the response's session_update_action; can an attacker influence that field's handling so tokens are stored under the current user without confirming the subject?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Return a refresh response with 'set' and a user id different from the active one.
- Invariant to test: Applying a refresh result must verify the returned user matches the session being refreshed.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return a refresh payload for a different user and assert logger levels NONE/ERROR/WARN/INFO/DEBUG refuses to store it.
