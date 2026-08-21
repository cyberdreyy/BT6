# Q0353: switchActiveUser accepts an unauthenticated id in Privy.ts

## Question
switchActiveUserId only checks membership in privy:saved-users; can an attacker make Privy constructor switch to an id whose tokens are absent, so subsequent calls fall back to the null-keyed credentials of another account?

## Target
- File/function: [src/client/Privy.ts](src/client/Privy.ts) - Privy constructor, initialize, getAccessToken, getIdentityToken, setMessagePoster, fetchPrivyRoute, getCompiledPath, track
- Entrypoint: new Privy({appId, clientId, sessions, storage, ...}) and privy.fetchPrivyRoute(...)
- Attacker controls: constructor options, arbitrary route+body via fetchPrivyRoute, message poster injection
- Exploit idea: Add an id to saved-users, switch to it, then call getAccessToken.
- Invariant to test: Switching users must require that user's own stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: switch to a saved id with no tokens and assert getAccessToken returns null instead of the previous user's token.
