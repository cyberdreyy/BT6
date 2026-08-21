# Q0130: backfill trusts the token subject in LocalStorage.ts

## Question
Session.backfillLegacySession derives the user id from Token.parse(token).subject of a legacy null-keyed value; can an attacker seed that key so LocalStorage.get (JSON.parse) adopts an attacker-chosen user id as the active user?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Write a crafted legacy token, initialize the SDK in multi-user mode and read privy:active-user.
- Invariant to test: The active user id must be derived from a server-verified session, not from a locally stored token body.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed privy:token with an unsigned JWT and assert backfill does not set privy:active-user from it.
