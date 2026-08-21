# Q3540: refresh failure destroys local state in LocalStorage.ts

## Question
On MISSING_OR_INVALID_TOKEN, _refreshSession calls destroyLocalState; can an attacker force that error to arrive during LocalStorage.get (JSON.parse) so a legitimate session is dropped and re-authentication is redirected?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Return the error code from the refresh route while the user is mid-flow.
- Invariant to test: Session destruction must follow an authenticated signal, not any error carrying that code.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return the error from an unauthenticated response and assert LocalStorage.get (JSON.parse) does not clear stored tokens.
