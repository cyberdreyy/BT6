# Q0350: switchActiveUser accepts an unauthenticated id in LocalStorage.ts

## Question
switchActiveUserId only checks membership in privy:saved-users; can an attacker make LocalStorage.get (JSON.parse) switch to an id whose tokens are absent, so subsequent calls fall back to the null-keyed credentials of another account?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Add an id to saved-users, switch to it, then call getAccessToken.
- Invariant to test: Switching users must require that user's own stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: switch to a saved id with no tokens and assert getAccessToken returns null instead of the previous user's token.
