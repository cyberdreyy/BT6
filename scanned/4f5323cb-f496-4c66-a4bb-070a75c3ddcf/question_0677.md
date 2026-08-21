# Q0677: refresh dedupe keyed by literal 'key' in Session.ts

## Question
refreshSession dedupes in-flight refreshes in a Map keyed by the refresh token, falling back to the literal 'key' when none exists; can an attacker make two different sessions share one in-flight refresh promise?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Trigger simultaneous refreshes with no refresh token present in multi-user mode and observe the shared cache entry.
- Invariant to test: Concurrent refreshes for different identities must never share a result.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two refreshes for different users with absent refresh tokens and assert two distinct requests are issued.
