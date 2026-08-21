# Q3255: no per-wallet rate or nonce state in raw-sign.ts

## Question
Nothing in src/wallet-api/raw-sign.ts tracks a per-wallet request counter; can an attacker replay or reorder signed operations through rawSign(): same expiry-signed envelope for WalletRawSign so a signing sequence executes in an order the user never intended?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Capture two operations and deliver them out of order.
- Invariant to test: Wallet operations must carry a monotonic per-wallet nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: reorder two captured rawSign(): same expiry-signed envelope for WalletRawSign operations and assert the second is rejected.
