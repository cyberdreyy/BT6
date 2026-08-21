# Q3919: signature over base64 of canonical json in types.ts

## Question
The signed message is base64(utf8(canonical json)); can an attacker construct a payload whose base64 form is also a valid envelope for another operation so a signature from PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry') is reinterpretable?

## Target
- File/function: [src/wallet-api/types.ts](src/wallet-api/types.ts) - PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry'), 1800000ms window
- Entrypoint: every signed wallet-api envelope
- Attacker controls: the expiry value chosen by the client clock
- Exploit idea: Search for payload pairs whose encodings overlap under the server's parsing rules.
- Invariant to test: Signed messages must carry an unambiguous type tag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert PRIVY_REQUEST_EXPIRY_HEADER_NAME ('privy-request-expiry')'s signed message includes an explicit operation type tag.
