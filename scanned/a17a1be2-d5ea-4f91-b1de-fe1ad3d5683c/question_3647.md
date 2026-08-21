# Q3647: identity token exposed to app code in Session.ts

## Question
privy.getIdentityToken returns the raw identity token from storage; can an attacker reach Session.updateWithTokensResponse in a context where that token is then embedded in a URL, log, or analytics payload?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Trace the identity token from storage to every consumer in the SDK.
- Invariant to test: Identity tokens read via src/Session.ts must never reach URLs, logs, or analytics.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert no code path passes the Session.updateWithTokensResponse result into getPath, toSearchParams, or createAnalyticsEvent.
