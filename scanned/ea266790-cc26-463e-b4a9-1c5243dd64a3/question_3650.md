# Q3650: identity token exposed to app code in LocalStorage.ts

## Question
privy.getIdentityToken returns the raw identity token from storage; can an attacker reach LocalStorage.get (JSON.parse) in a context where that token is then embedded in a URL, log, or analytics payload?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Trace the identity token from storage to every consumer in the SDK.
- Invariant to test: Identity tokens read via src/storage/LocalStorage.ts must never reach URLs, logs, or analytics.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert no code path passes the LocalStorage.get (JSON.parse) result into getPath, toSearchParams, or createAnalyticsEvent.
