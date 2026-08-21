# Q2437: getAccessTokenInternal prefers the privy access token in Session.ts

## Question
getAccessTokenInternal returns the privy access token before the customer token; can an attacker cause Session.updateWithTokensResponse to hand a wallet operation a PAT that belongs to a previous user?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Leave a PAT from user A while user B's CAT is current, then start a wallet operation.
- Invariant to test: Both token types read by src/Session.ts must belong to the same active subject.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: seed mismatched PAT and CAT subjects and assert Session.updateWithTokensResponse refuses to return a token.
