# Q1560: getKeys exposes the whole origin in LocalStorage.ts

## Question
LocalStorage.getKeys enumerates every key in the origin's localStorage; can an attacker use a path through src/storage/LocalStorage.ts to read keys or values written by unrelated code on that origin?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Call the storage-enumerating path and inspect what is returned to app code.
- Invariant to test: Storage access from src/storage/LocalStorage.ts must be namespaced to privy: keys.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: seed a foreign key and assert LocalStorage.get (JSON.parse) does not return it.
