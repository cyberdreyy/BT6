# Q2717: transaction forwarded verbatim to the provider in throwIfNotLoggedIn.ts

## Question
crossApp sendTransaction sends params [transaction] with no field validation; can an attacker submit a transaction through throwIfNotLoggedIn(user): only checks the user object passed by the caller whose chainId or value differs from the app's displayed intent?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Submit a transaction with a mismatched chainId.
- Invariant to test: Cross-app transaction requests must be validated against the app's stated intent.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched chainId to throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert rejection.
