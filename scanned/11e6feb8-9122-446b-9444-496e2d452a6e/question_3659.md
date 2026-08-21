# Q3659: identity token exposed to app code in toSearchParams.ts

## Question
privy.getIdentityToken returns the raw identity token from storage; can an attacker reach toSearchParams (skips null/undefined in a context where that token is then embedded in a URL, log, or analytics payload?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Trace the identity token from storage to every consumer in the SDK.
- Invariant to test: Identity tokens read via src/utils/toSearchParams.ts must never reach URLs, logs, or analytics.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert no code path passes the toSearchParams (skips null/undefined result into getPath, toSearchParams, or createAnalyticsEvent.
