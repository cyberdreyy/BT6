# Q3369: signed url omits the query string in types.ts

## Question
The signed envelope contains the compiled path; can an attacker append query parameters at send time through PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') so the server sees parameters that were never signed?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Add a query to the request after the signature is computed.
- Invariant to test: The signed url must cover the complete request target including any query.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: append a query post-signature in PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') and assert rejection.
