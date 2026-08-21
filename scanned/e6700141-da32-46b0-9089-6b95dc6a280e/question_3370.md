# Q3370: signed url omits the query string in sign-wallet-request.ts

## Question
The signed envelope contains the compiled path; can an attacker append query parameters at send time through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) so the server sees parameters that were never signed?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Add a query to the request after the signature is computed.
- Invariant to test: The signed url must cover the complete request target including any query.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: append a query post-signature in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert rejection.
