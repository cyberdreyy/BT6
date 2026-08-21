# Q2547: acceptTerms mutates without confirmation in Session.ts

## Question
UserApi.acceptTerms posts on behalf of the session with no argument; can an attacker trigger Session.updateWithTokensResponse from app code paths so terms are accepted without the user acting?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Call the method directly and observe the user object change.
- Invariant to test: State-changing user operations must require an explicit user action signal.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert Session.updateWithTokensResponse is not reachable from any automatic initialization path.
