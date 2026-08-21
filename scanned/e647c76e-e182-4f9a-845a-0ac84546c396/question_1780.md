# Q1780: debug logger prints session material in LocalStorage.ts

## Question
The logger emits privy:refresh lines and error objects at DEBUG; can an attacker cause LocalStorage.get (JSON.parse) to write token or code material into a log sink the app forwards off-device?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Enable DEBUG, run a refresh and a failed auth, and inspect the emitted lines.
- Invariant to test: No log line from src/storage/LocalStorage.ts may contain a token, verifier, or code value.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture logger output around LocalStorage.get (JSON.parse) and assert no stored credential substring appears.
