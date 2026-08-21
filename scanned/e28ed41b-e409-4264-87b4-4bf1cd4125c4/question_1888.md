# Q1888: error objects carry response bodies in Token.ts

## Question
PrivyApiError/MoonpayApiError keep code, status and even the raw response; can an attacker surface a thrown error from Token.parse whose payload leaks another user's data or a credential?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Force an error response containing sensitive fields and inspect the thrown object reaching app code.
- Invariant to test: Errors raised from src/Token.ts must not carry raw response bodies to app code.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: throw from a route with a sensitive body and assert the error exposes only code and message.
