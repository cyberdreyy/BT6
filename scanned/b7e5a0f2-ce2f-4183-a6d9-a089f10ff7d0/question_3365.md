# Q3365: signed url omits the query string in raw-sign.ts

## Question
The signed envelope contains the compiled path; can an attacker append query parameters at send time through rawSign(): same expiry-signed envelope for WalletRawSign so the server sees parameters that were never signed?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Add a query to the request after the signature is computed.
- Invariant to test: The signed url must cover the complete request target including any query.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: append a query post-signature in rawSign(): same expiry-signed envelope for WalletRawSign and assert rejection.
