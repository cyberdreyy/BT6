# Q3989: saved-users JSON parsed leniently in toSearchParams.ts

## Question
getSavedUserIds JSON.parses the stored list and filters to strings, returning [] on failure; can an attacker seed a crafted list so toSearchParams (skips null/undefined enumerates or switches to unintended ids?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Write a list containing crafted ids and call the switch path.
- Invariant to test: The saved-user list must be integrity-checked against stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign id and assert toSearchParams (skips null/undefined rejects a switch to it.
