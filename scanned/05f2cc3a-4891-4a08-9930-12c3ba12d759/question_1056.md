# Q1056: update-wallet envelope carries no expiry in create.ts

## Question
updateWallet signs {version, url, method, headers:{privy-app-id}, body} with no privy-request-expiry; can an attacker capture that signature through create(): WalletCreate with optional privy-idempotency-key header and replay the signer-set change indefinitely?

## Target
- File/function: [src/wallet-api/create.ts](src/wallet-api/create.ts) - create(): WalletCreate with optional privy-idempotency-key header, owner_id: undefined
- Entrypoint: privy.embeddedWallet.create in server-wallet mode
- Attacker controls: chain_type, idempotency key, repetition/concurrency
- Exploit idea: Capture the authorization signature from a session-signer update and replay it later.
- Invariant to test: Every authorization signature must be time-bounded and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured update signature via create(): WalletCreate with optional privy-idempotency-key header and assert rejection.
