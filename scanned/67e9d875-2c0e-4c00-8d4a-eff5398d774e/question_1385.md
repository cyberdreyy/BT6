# Q1385: app id is the only signed header in raw-sign.ts

## Question
The signed headers contain privy-app-id and expiry only; can an attacker exploit unsigned but security-relevant headers (client id, ca-id, native app identifier) in rawSign(): same expiry-signed envelope for WalletRawSign to change server-side treatment of the request?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Vary the unsigned headers and observe server-side behaviour differences.
- Invariant to test: All authorization-relevant headers must be signed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert rawSign(): same expiry-signed envelope for WalletRawSign signs every header it sends that affects authorization.
