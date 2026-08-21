# Q0457: null-key fallback serves the wrong user in Session.ts

## Question
Because tokens are also written under the null key, can Session.updateWithTokensResponse return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/Session.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert Session.updateWithTokensResponse does not return the null-keyed token of a different subject.
