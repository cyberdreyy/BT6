# Q1605: access token captured in the signing closure in raw-sign.ts

## Question
The signer closure captures the access token at construction; can an attacker keep a stale closure alive so rawSign(): same expiry-signed envelope for WalletRawSign signs using a token belonging to a previous session?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Obtain the closure, change sessions, then sign.
- Invariant to test: Signing must resolve the current session token at call time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change sessions and assert rawSign(): same expiry-signed envelope for WalletRawSign refuses to reuse the captured token.
