# Q3329: cookie names collide across apps in toSearchParams.ts

## Question
Cookie names are app-agnostic (privy-token, privy-session); can an attacker on a sibling subdomain of the same registrable domain observe or overwrite them so toSearchParams (skips null/undefined reads a foreign credential?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Set a cookie of the same name from a sibling context and read it back.
- Invariant to test: Credential cookies read by src/utils/toSearchParams.ts must be namespaced and validated before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign privy-token cookie and assert toSearchParams (skips null/undefined validates the subject before use.
