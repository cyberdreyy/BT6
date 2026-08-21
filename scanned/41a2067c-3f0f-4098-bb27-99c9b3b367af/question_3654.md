# Q3654: identity token exposed to app code in UserApi.ts

## Question
privy.getIdentityToken returns the raw identity token from storage; can an attacker reach UserApi.get in a context where that token is then embedded in a URL, log, or analytics payload?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Trace the identity token from storage to every consumer in the SDK.
- Invariant to test: Identity tokens read via src/client/UserApi.ts must never reach URLs, logs, or analytics.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert no code path passes the UserApi.get result into getPath, toSearchParams, or createAnalyticsEvent.
