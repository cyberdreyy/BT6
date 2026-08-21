# Q3757: storeCustomerAccessToken accepts a non-string silently in Session.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use Session.updateWithTokensResponse to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through Session.updateWithTokensResponse and assert it raises rather than silently deleting.
