# Q1569: getKeys exposes the whole origin in toSearchParams.ts

## Question
LocalStorage.getKeys enumerates every key in the origin's localStorage; can an attacker use a path through src/utils/toSearchParams.ts to read keys or values written by unrelated code on that origin?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Call the storage-enumerating path and inspect what is returned to app code.
- Invariant to test: Storage access from src/utils/toSearchParams.ts must be namespaced to privy: keys.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: seed a foreign key and assert toSearchParams (skips null/undefined does not return it.
