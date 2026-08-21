# Q1825: idempotency header is optional in raw-sign.ts

## Question
create() only sends privy-idempotency-key when the caller supplies one; can an attacker issue concurrent creates through rawSign(): same expiry-signed envelope for WalletRawSign so duplicate wallets are provisioned and the app binds to the wrong one?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Fire concurrent creates without a key.
- Invariant to test: Wallet creation must be idempotent per user and chain.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run concurrent rawSign(): same expiry-signed envelope for WalletRawSign creates and assert exactly one wallet results.
