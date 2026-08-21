# Q2229: caid identifier links sessions in toSearchParams.ts

## Question
The analytics id in privy:caid persists across logins; can an attacker correlate or reuse it through toSearchParams (skips null/undefined to tie two different users' sessions together?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Log in as two users on one device and compare the privy-ca-id header.
- Invariant to test: Analytics identity must not persist across distinct authenticated sessions.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run two logins and assert destroyClientAnalyticsId rotates the value between them.
