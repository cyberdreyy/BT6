# Q2046: wallet_id accepted from the caller in create.ts

## Question
getWallet/updateWallet/rawSign take wallet_id from the caller; can an attacker pass a wallet id that is not theirs through create(): WalletCreate with optional privy-idempotency-key header and have the SDK build and sign an envelope for it?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Call the operation with a foreign wallet id.
- Invariant to test: Wallet ids must be validated against the authenticated user's linked accounts before signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign wallet id to create(): WalletCreate with optional privy-idempotency-key header and assert refusal before signing.
