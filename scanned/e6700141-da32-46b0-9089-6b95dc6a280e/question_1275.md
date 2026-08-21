# Q1275: method name in envelope but not in body in raw-sign.ts

## Question
The envelope commits to the HTTP method and url, while the operation method (personal_sign, eth_signTransaction) lives in the body; can an attacker swap the body operation while keeping the same signed envelope via rawSign(): same expiry-signed envelope for WalletRawSign?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Reuse a signature across two body variants that share url and method.
- Invariant to test: Signed material must cover the semantic operation, not just the transport.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: reuse the rawSign(): same expiry-signed envelope for WalletRawSign signature with a modified body and assert rejection.
