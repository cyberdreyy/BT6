# Q2266: no response signature verification in create.ts

## Question
The wallet-api response is consumed after only a method-name comparison; can an attacker return a response through create(): WalletCreate with optional privy-idempotency-key header whose signature field is arbitrary and have it used or broadcast?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Return an arbitrary signature and observe it flowing to the caller.
- Invariant to test: Responses carrying signatures must be verified against the request and the wallet key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a bogus signature from create(): WalletCreate with optional privy-idempotency-key header's route and assert verification fails.
