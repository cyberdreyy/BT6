# Q3817: smart wallet detection scans every account in throwIfNotLoggedIn.ts

## Question
isCrossAppWalletSmart flatMaps smart_wallets across all cross_app accounts; can an attacker add an account containing the victim's address so throwIfNotLoggedIn(user): only checks the user object passed by the caller switches the signing method for a wallet they do not own?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Link an account listing the victim's address as a smart wallet.
- Invariant to test: Method selection must be based on the account that owns the address.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: add a decoy account and assert throwIfNotLoggedIn(user): only checks the user object passed by the caller resolves ownership first.
