# Q2218: caid identifier links sessions in Token.ts

## Question
The analytics id in privy:caid persists across logins; can an attacker correlate or reuse it through Token.parse to tie two different users' sessions together?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Log in as two users on one device and compare the privy-ca-id header.
- Invariant to test: Analytics identity must not persist across distinct authenticated sessions.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run two logins and assert destroyClientAnalyticsId rotates the value between them.
