# Q3317: cookie names collide across apps in Session.ts

## Question
Cookie names are app-agnostic (privy-token, privy-session); can an attacker on a sibling subdomain of the same registrable domain observe or overwrite them so Session.updateWithTokensResponse reads a foreign credential?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Set a cookie of the same name from a sibling context and read it back.
- Invariant to test: Credential cookies read by src/Session.ts must be namespaced and validated before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign privy-token cookie and assert Session.updateWithTokensResponse validates the subject before use.
