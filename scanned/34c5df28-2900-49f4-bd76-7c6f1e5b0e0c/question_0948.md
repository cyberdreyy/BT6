# Q0948: expiry check is a tautology in update-wallet.ts

## Question
The guard compares Date.now() against a value just computed from Date.now(); can an attacker rely on this dead check so updateWallet(): signs {version:1 never actually rejects a stale envelope?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Trace the branch and confirm it can only trigger under an implausible delay.
- Invariant to test: Freshness must be validated against the moment of transmission.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delay between construction and send in updateWallet(): signs {version:1 and assert the stale envelope is rejected.
