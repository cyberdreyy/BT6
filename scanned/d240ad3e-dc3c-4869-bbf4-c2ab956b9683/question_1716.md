# Q1716: create() sends owner_id undefined in create.ts

## Question
create() posts `{chain_type, owner_id: undefined}`; can an attacker exploit the omitted owner so create(): WalletCreate with optional privy-idempotency-key header produces a wallet whose ownership is inferred server-side from an ambiguous context?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Call create in each session state and observe the resulting owner.
- Invariant to test: Wallet ownership must be explicit in the creation request.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert create(): WalletCreate with optional privy-idempotency-key header sends an explicit owner derived from the session user.
