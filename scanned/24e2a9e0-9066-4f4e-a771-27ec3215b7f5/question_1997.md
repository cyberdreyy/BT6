# Q1997: appId or clientId swapped at construction in Session.ts

## Question
Privy's constructor accepts appId, clientId, baseUrl, storage and crypto; can an attacker in the page reach Session.updateWithTokensResponse with substituted options so requests are signed and stored under a different app namespace?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Construct a second client with a different appId sharing the same storage and observe key collisions.
- Invariant to test: Storage namespacing must prevent one app id's session from being consumed by another.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two clients with different appIds over one Storage and assert no key collisions.
