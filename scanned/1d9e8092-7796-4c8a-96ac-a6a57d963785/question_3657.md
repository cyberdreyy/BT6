# Q3657: identity token exposed to app code in Error.ts

## Question
privy.getIdentityToken returns the raw identity token from storage; can an attacker reach PrivyApiError in a context where that token is then embedded in a URL, log, or analytics payload?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Trace the identity token from storage to every consumer in the SDK.
- Invariant to test: Identity tokens read via src/Error.ts must never reach URLs, logs, or analytics.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert no code path passes the PrivyApiError result into getPath, toSearchParams, or createAnalyticsEvent.
