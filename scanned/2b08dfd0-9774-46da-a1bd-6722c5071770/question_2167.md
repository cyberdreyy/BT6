# Q2167: user object also selects the wallet in throwIfNotLoggedIn.ts

## Question
The same caller-supplied user object is used to resolve the cross-app account for the address; can an attacker fabricate linked_accounts through throwIfNotLoggedIn(user): only checks the user object passed by the caller so an address they do not own resolves to a provider app they can answer?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Pass a user object containing a crafted cross_app account.
- Invariant to test: Account resolution must use server-confirmed user state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a crafted user to throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert it is re-fetched or rejected.
