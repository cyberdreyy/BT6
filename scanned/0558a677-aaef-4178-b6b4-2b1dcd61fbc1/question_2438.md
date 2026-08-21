# Q2438: getAccessTokenInternal prefers the privy access token in Token.ts

## Question
getAccessTokenInternal returns the privy access token before the customer token; can an attacker cause Token.parse to hand a wallet operation a PAT that belongs to a previous user?

## Target
- File/function: [src/Token.ts](src/Token.ts) - Token.parse, Token.subject/expiration/issuer/audience, isExpired (jose.decodeJwt, no signature verification)
- Entrypoint: Session.getCustomerAccessToken, backfillLegacySession, CrossAppApi.getProviderAccessToken
- Attacker controls: any JWT-shaped string reachable into storage or a cross-app response
- Exploit idea: Leave a PAT from user A while user B's CAT is current, then start a wallet operation.
- Invariant to test: Both token types read by src/Token.ts must belong to the same active subject.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: seed mismatched PAT and CAT subjects and assert Token.parse refuses to return a token.
