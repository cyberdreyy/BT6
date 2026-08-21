# Q1828: idempotency header is optional in update-wallet.ts

## Question
create() only sends privy-idempotency-key when the caller supplies one; can an attacker issue concurrent creates through updateWallet(): signs {version:1 so duplicate wallets are provisioned and the app binds to the wrong one?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Fire concurrent creates without a key.
- Invariant to test: Wallet creation must be idempotent per user and chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run concurrent updateWallet(): signs {version:1 creates and assert exactly one wallet results.
