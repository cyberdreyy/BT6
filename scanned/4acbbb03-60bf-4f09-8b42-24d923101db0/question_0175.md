# Q0175: body signed separately from the sent body in raw-sign.ts

## Question
The signature covers `{...request}` while fetchPrivyRoute is called with the same object by reference; can an attacker mutate the request object between signing and sending so rawSign(): same expiry-signed envelope for WalletRawSign transmits a body the signature does not cover?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Pass an object with a mutating getter or mutate it from a microtask between the two awaits.
- Invariant to test: The signed bytes and the transmitted bytes must be the same immutable snapshot.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mutate the body between sign and send in rawSign(): same expiry-signed envelope for WalletRawSign and assert the request is rejected.
