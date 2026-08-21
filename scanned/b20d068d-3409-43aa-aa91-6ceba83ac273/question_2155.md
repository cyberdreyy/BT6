# Q2155: raw-sign hashes anything in raw-sign.ts

## Question
rawSign forwards the caller's params to WalletRawSign under the same signed envelope; can an attacker use rawSign(): same expiry-signed envelope for WalletRawSign to obtain a raw signature over a transaction digest that the wallet would never sign through a typed path?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Submit a transaction hash through the raw-sign entrypoint.
- Invariant to test: Raw-hash signing must require an explicit, distinct user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: submit a transaction digest through rawSign(): same expiry-signed envelope for WalletRawSign and assert an approval gate is enforced.
