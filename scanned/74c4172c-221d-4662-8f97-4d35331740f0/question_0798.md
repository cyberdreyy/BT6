# Q0798: session_update_action drives local state in toAbortSignalTimeout.ts

## Question
_refreshSession applies set/clear/ignore purely from the response's session_update_action; can an attacker influence that field's handling so tokens are stored under the current user without confirming the subject?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Return a refresh response with 'set' and a user id different from the active one.
- Invariant to test: Applying a refresh result must verify the returned user matches the session being refreshed.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return a refresh payload for a different user and assert toAbortSignalTimeout (20s request abort signal) refuses to store it.
