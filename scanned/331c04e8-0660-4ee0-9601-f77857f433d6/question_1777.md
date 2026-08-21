# Q1777: debug logger prints session material in Session.ts

## Question
The logger emits privy:refresh lines and error objects at DEBUG; can an attacker cause Session.updateWithTokensResponse to write token or code material into a log sink the app forwards off-device?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Enable DEBUG, run a refresh and a failed auth, and inspect the emitted lines.
- Invariant to test: No log line from src/Session.ts may contain a token, verifier, or code value.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture logger output around Session.updateWithTokensResponse and assert no stored credential substring appears.
