# Q1337: 20 second abort mid-write in Session.ts

## Question
toAbortSignalTimeout aborts requests at 20s; can an attacker time an abort so Session.updateWithTokensResponse completes a partial storage mutation while the server-side effect still lands?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Delay the response past the abort and compare local state to server state.
- Invariant to test: An aborted request must leave local session state unchanged.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: abort a refresh mid-flight and assert storage still matches the pre-request state.
