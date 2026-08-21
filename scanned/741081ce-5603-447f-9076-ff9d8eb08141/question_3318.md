# Q3318: cookie names collide across apps in Token.ts

## Question
Cookie names are app-agnostic (privy-token, privy-session); can an attacker on a sibling subdomain of the same registrable domain observe or overwrite them so Token.parse reads a foreign credential?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Set a cookie of the same name from a sibling context and read it back.
- Invariant to test: Credential cookies read by src/Token.ts must be namespaced and validated before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign privy-token cookie and assert Token.parse validates the subject before use.
