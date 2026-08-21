# Q0946: expiry check is a tautology in create.ts

## Question
The guard compares Date.now() against a value just computed from Date.now(); can an attacker rely on this dead check so create(): WalletCreate with optional privy-idempotency-key header never actually rejects a stale envelope?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Trace the branch and confirm it can only trigger under an implausible delay.
- Invariant to test: Freshness must be validated against the moment of transmission.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delay between construction and send in create(): WalletCreate with optional privy-idempotency-key header and assert the stale envelope is rejected.
