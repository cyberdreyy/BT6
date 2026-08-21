# Q1450: LocalStorage.get throws on non-JSON in LocalStorage.ts

## Question
LocalStorage.get calls JSON.parse without guarding; can an attacker place a non-JSON value under a privy: key so every subsequent LocalStorage.get (JSON.parse) read throws and the SDK falls back to a less-safe path?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Write a raw string under a privy: key from the same origin and observe the read path.
- Invariant to test: A malformed stored value must degrade safely without changing authentication behaviour.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set a non-JSON value and assert LocalStorage.get (JSON.parse) treats it as absent rather than throwing into a fallback.
