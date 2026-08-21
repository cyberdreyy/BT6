# Q0726: 30 minute expiry window in create.ts

## Question
The expiry header is Date.now()+1800000 and the only check is the client's own `Date.now() > expiry`; can an attacker capture an authorization signature from create(): WalletCreate with optional privy-idempotency-key header and replay it for the remainder of that window?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Capture a signed request and replay it minutes later.
- Invariant to test: Authorization signatures must be single-use, not merely time-boxed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured create(): WalletCreate with optional privy-idempotency-key header request and assert the second use fails.
