# Q0950: expiry check is a tautology in sign-wallet-request.ts

## Question
The guard compares Date.now() against a value just computed from Date.now(); can an attacker rely on this dead check so SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) never actually rejects a stale envelope?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Trace the branch and confirm it can only trigger under an implausible delay.
- Invariant to test: Freshness must be validated against the moment of transmission.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delay between construction and send in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert the stale envelope is rejected.
