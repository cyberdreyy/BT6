# Q2926: signature not bound to the access token in create.ts

## Question
The envelope commits to app id and expiry but not to the session token used to authenticate; can an attacker present a signature from create(): WalletCreate with optional privy-idempotency-key header together with a different session token?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Pair a captured signature with another token.
- Invariant to test: Authorization signatures must be bound to the session that produced them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross a captured signature with another session and assert rejection.
