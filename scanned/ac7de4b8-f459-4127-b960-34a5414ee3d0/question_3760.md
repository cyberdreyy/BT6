# Q3760: storeCustomerAccessToken accepts a non-string silently in LocalStorage.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use LocalStorage.get (JSON.parse) to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through LocalStorage.get (JSON.parse) and assert it raises rather than silently deleting.
