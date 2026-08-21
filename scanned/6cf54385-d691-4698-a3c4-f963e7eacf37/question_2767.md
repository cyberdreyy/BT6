# Q2767: switchActiveUser reloads the wallet iframe mid-operation in Session.ts

## Question
UserApi.switchActiveUser calls embedded.reload(), which flushes the shared callback queue; can an attacker use Session.updateWithTokensResponse to reject or hijack another in-flight wallet operation?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Start a signature, then switch users and observe the flush rejecting the pending callback.
- Invariant to test: A user switch must not be able to interfere with an unrelated in-flight signing operation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: start a sign request, call Session.updateWithTokensResponse, and assert the pending request neither resolves with foreign data nor leaks its callback.
