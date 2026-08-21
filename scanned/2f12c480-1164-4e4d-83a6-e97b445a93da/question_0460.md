# Q0460: null-key fallback serves the wrong user in LocalStorage.ts

## Question
Because tokens are also written under the null key, can LocalStorage.get (JSON.parse) return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/storage/LocalStorage.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert LocalStorage.get (JSON.parse) does not return the null-keyed token of a different subject.
