# Q3035: failure between sign and send loses atomicity in raw-sign.ts

## Question
If fetchPrivyRoute throws after signing, the signature remains valid; can an attacker force that failure in rawSign(): same expiry-signed envelope for WalletRawSign and then reuse the signature at a moment of their choosing?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Abort the request post-signature and replay it later.
- Invariant to test: An unused authorization signature must be invalidated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: abort after signing in rawSign(): same expiry-signed envelope for WalletRawSign and assert the signature cannot be reused.
