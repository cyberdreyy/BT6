# Q1837: address comparison is exact string equality in throwIfNotLoggedIn.ts

## Question
Address membership is tested by === without normalisation; can an attacker submit a checksummed or padded variant through throwIfNotLoggedIn(user): only checks the user object passed by the caller so the account is not found, or a different account is selected?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Pass mixed-case and padded address variants.
- Invariant to test: Address comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through throwIfNotLoggedIn(user): only checks the user object passed by the caller.
