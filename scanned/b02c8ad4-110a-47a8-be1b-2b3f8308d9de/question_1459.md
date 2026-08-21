# Q1459: LocalStorage.get throws on non-JSON in toSearchParams.ts

## Question
LocalStorage.get calls JSON.parse without guarding; can an attacker place a non-JSON value under a privy: key so every subsequent toSearchParams (skips null/undefined read throws and the SDK falls back to a less-safe path?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Write a raw string under a privy: key from the same origin and observe the read path.
- Invariant to test: A malformed stored value must degrade safely without changing authentication behaviour.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set a non-JSON value and assert toSearchParams (skips null/undefined treats it as absent rather than throwing into a fallback.
