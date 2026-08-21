# Q0407: no request/response correlation id in throwIfNotLoggedIn.ts

## Question
The request carries only content and a timestamp; can an attacker deliver a response to throwIfNotLoggedIn(user): only checks the user object passed by the caller that belongs to a different cross-app request so the caller associates the wrong result?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Issue two cross-app requests and cross the responses.
- Invariant to test: Cross-app responses must be correlated by an unguessable request id.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross two throwIfNotLoggedIn(user): only checks the user object passed by the caller responses and assert the mismatch is detected.
