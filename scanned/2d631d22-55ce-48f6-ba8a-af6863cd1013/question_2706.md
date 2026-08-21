# Q2706: params object forwarded verbatim in create.ts

## Question
The params branch of the signed body is passed through unvalidated; can an attacker include extra params fields through create(): WalletCreate with optional privy-idempotency-key header that the server honours but the client never showed the user?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Add unexpected keys to the params object.
- Invariant to test: Only a validated params schema may be signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: add unknown params keys in create(): WalletCreate with optional privy-idempotency-key header and assert they are stripped or rejected.
