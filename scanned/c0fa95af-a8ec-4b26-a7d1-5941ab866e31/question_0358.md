# Q0358: switchActiveUser accepts an unauthenticated id in toAbortSignalTimeout.ts

## Question
switchActiveUserId only checks membership in privy:saved-users; can an attacker make toAbortSignalTimeout (20s request abort signal) switch to an id whose tokens are absent, so subsequent calls fall back to the null-keyed credentials of another account?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Add an id to saved-users, switch to it, then call getAccessToken.
- Invariant to test: Switching users must require that user's own stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: switch to a saved id with no tokens and assert getAccessToken returns null instead of the previous user's token.
