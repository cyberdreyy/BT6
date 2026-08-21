# Q0835: expiry chosen by the client clock in raw-sign.ts

## Question
The expiry is derived from the local clock; can an attacker skew the clock so rawSign(): same expiry-signed envelope for WalletRawSign mints an envelope valid far into the future?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Advance the system clock and inspect the generated expiry.
- Invariant to test: Request validity must not be extendable by the client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mock Date.now far ahead and assert rawSign(): same expiry-signed envelope for WalletRawSign clamps the expiry.
