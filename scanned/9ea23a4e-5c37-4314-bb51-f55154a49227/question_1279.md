# Q1279: method name in envelope but not in body in types.ts

## Question
The envelope commits to the HTTP method and url, while the operation method (personal_sign, eth_signTransaction) lives in the body; can an attacker swap the body operation while keeping the same signed envelope via PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry')?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Reuse a signature across two body variants that share url and method.
- Invariant to test: Signed material must cover the semantic operation, not just the transport.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: reuse the PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') signature with a modified body and assert rejection.
