# Q3769: storeCustomerAccessToken accepts a non-string silently in toSearchParams.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use toSearchParams (skips null/undefined to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through toSearchParams (skips null/undefined and assert it raises rather than silently deleting.
