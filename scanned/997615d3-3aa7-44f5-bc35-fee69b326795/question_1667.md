# Q1667: InMemoryCache prototype keys in Session.ts

## Question
InMemoryCache stores entries on a plain object; can an attacker supply a key such as __proto__ or constructor through Session.updateWithTokensResponse so a read returns an inherited value or a write corrupts the cache?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Call the storage put/get path with prototype-named keys.
- Invariant to test: Cache keys must not reach object prototype slots.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: put and get '__proto__' through Session.updateWithTokensResponse and assert isolation.
