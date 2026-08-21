# Q0790: session_update_action drives local state in LocalStorage.ts

## Question
_refreshSession applies set/clear/ignore purely from the response's session_update_action; can an attacker influence that field's handling so tokens are stored under the current user without confirming the subject?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Return a refresh response with 'set' and a user id different from the active one.
- Invariant to test: Applying a refresh result must verify the returned user matches the session being refreshed.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return a refresh payload for a different user and assert LocalStorage.get (JSON.parse) refuses to store it.
