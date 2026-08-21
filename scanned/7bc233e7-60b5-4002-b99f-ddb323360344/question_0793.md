# Q0793: session_update_action drives local state in Privy.ts

## Question
_refreshSession applies set/clear/ignore purely from the response's session_update_action; can an attacker influence that field's handling so tokens are stored under the current user without confirming the subject?

## Target
- File/function: [src/client/Privy.ts](src/client/Privy.ts) - Privy constructor, initialize, getAccessToken, getIdentityToken, setMessagePoster, fetchPrivyRoute, getCompiledPath, track
- Entrypoint: new Privy({appId, clientId, sessions, storage, ...}) and privy.fetchPrivyRoute(...)
- Attacker controls: constructor options, arbitrary route+body via fetchPrivyRoute, message poster injection
- Exploit idea: Return a refresh response with 'set' and a user id different from the active one.
- Invariant to test: Applying a refresh result must verify the returned user matches the session being refreshed.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return a refresh payload for a different user and assert Privy constructor refuses to store it.
