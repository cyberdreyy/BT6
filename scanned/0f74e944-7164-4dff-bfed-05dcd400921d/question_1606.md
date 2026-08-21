# Q1606: access token captured in the signing closure in create.ts

## Question
The signer closure captures the access token at construction; can an attacker keep a stale closure alive so create(): WalletCreate with optional privy-idempotency-key header signs using a token belonging to a previous session?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Obtain the closure, change sessions, then sign.
- Invariant to test: Signing must resolve the current session token at call time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change sessions and assert create(): WalletCreate with optional privy-idempotency-key header refuses to reuse the captured token.
