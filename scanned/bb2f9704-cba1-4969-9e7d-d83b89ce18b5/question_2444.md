# Q2444: getAccessTokenInternal prefers the privy access token in UserApi.ts

## Question
getAccessTokenInternal returns the privy access token before the customer token; can an attacker cause UserApi.get to hand a wallet operation a PAT that belongs to a previous user?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Leave a PAT from user A while user B's CAT is current, then start a wallet operation.
- Invariant to test: Both token types read by src/client/UserApi.ts must belong to the same active subject.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: seed mismatched PAT and CAT subjects and assert UserApi.get refuses to return a token.
