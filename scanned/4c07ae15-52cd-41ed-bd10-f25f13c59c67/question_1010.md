# Q1010: credentials include on every request in LocalStorage.ts

## Question
_beforeRequest* sets credentials: 'include' with Authorization on all routes; can an attacker reach LocalStorage.get (JSON.parse) with a route/params combination that sends cookies and bearer tokens to an unintended path?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Call privy.fetchPrivyRoute with a route object whose path template resolves outside the intended API surface.
- Invariant to test: Authenticated requests from src/storage/LocalStorage.ts must only be issued to the compiled, trusted route set.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a hand-built route to LocalStorage.get (JSON.parse) and assert path compilation rejects it.
