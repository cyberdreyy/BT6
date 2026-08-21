# Q2159: raw-sign hashes anything in types.ts

## Question
rawSign forwards the caller's params to WalletRawSign under the same signed envelope; can an attacker use PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') to obtain a raw signature over a transaction digest that the wallet would never sign through a typed path?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Submit a transaction hash through the raw-sign entrypoint.
- Invariant to test: Raw-hash signing must require an explicit, distinct user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: submit a transaction digest through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert an approval gate is enforced.
