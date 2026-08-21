# Q1007: credentials include on every request in Session.ts

## Question
_beforeRequest* sets credentials: 'include' with Authorization on all routes; can an attacker reach Session.updateWithTokensResponse with a route/params combination that sends cookies and bearer tokens to an unintended path?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Call privy.fetchPrivyRoute with a route object whose path template resolves outside the intended API surface.
- Invariant to test: Authenticated requests from src/Session.ts must only be issued to the compiled, trusted route set.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a hand-built route to Session.updateWithTokensResponse and assert path compilation rejects it.
