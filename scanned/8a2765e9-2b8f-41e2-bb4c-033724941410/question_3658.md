# Q3658: identity token exposed to app code in toAbortSignalTimeout.ts

## Question
privy.getIdentityToken returns the raw identity token from storage; can an attacker reach toAbortSignalTimeout (20s request abort signal) in a context where that token is then embedded in a URL, log, or analytics payload?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Trace the identity token from storage to every consumer in the SDK.
- Invariant to test: Identity tokens read via src/toAbortSignalTimeout.ts must never reach URLs, logs, or analytics.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert no code path passes the toAbortSignalTimeout (20s request abort signal) result into getPath, toSearchParams, or createAnalyticsEvent.
