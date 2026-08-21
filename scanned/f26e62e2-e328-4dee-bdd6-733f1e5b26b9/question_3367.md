# Q3367: signed url omits the query string in get-wallet.ts

## Question
The signed envelope contains the compiled path; can an attacker append query parameters at send time through getWallet(): WalletGet by wallet_id so the server sees parameters that were never signed?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Add a query to the request after the signature is computed.
- Invariant to test: The signed url must cover the complete request target including any query.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: append a query post-signature in getWallet(): WalletGet by wallet_id and assert rejection.
