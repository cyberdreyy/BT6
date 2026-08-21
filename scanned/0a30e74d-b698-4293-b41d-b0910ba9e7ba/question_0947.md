# Q0947: expiry check is a tautology in get-wallet.ts

## Question
The guard compares Date.now() against a value just computed from Date.now(); can an attacker rely on this dead check so getWallet(): WalletGet by wallet_id never actually rejects a stale envelope?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Trace the branch and confirm it can only trigger under an implausible delay.
- Invariant to test: Freshness must be validated against the moment of transmission.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delay between construction and send in getWallet(): WalletGet by wallet_id and assert the stale envelope is rejected.
