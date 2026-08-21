# Q3219: key builder collides on crafted user ids in toSearchParams.ts

## Question
Token storage keys are built by string interpolation of the user id; can an attacker obtain or seed a user id containing ':' so keys for two users collide?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Store sessions for ids 'a' and 'a:token' style values and compare resulting keys.
- Invariant to test: Key construction in src/utils/toSearchParams.ts must be injective over user ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert toSearchParams (skips null/undefined produces distinct keys for ids that differ only by separators.
