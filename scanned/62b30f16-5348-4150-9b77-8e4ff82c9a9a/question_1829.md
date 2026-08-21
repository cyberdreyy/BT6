# Q1829: idempotency header is optional in types.ts

## Question
create() only sends privy-idempotency-key when the caller supplies one; can an attacker issue concurrent creates through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') so duplicate wallets are provisioned and the app binds to the wrong one?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Fire concurrent creates without a key.
- Invariant to test: Wallet creation must be idempotent per user and chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run concurrent PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') creates and assert exactly one wallet results.
