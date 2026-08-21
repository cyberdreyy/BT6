# Q2449: getAccessTokenInternal prefers the privy access token in toSearchParams.ts

## Question
getAccessTokenInternal returns the privy access token before the customer token; can an attacker cause toSearchParams (skips null/undefined to hand a wallet operation a PAT that belongs to a previous user?

## Target
- File/function: [src/utils/toSearchParams.ts](src/utils/toSearchParams.ts) - toSearchParams (skips null/undefined, String() coercion)
- Entrypoint: PrivyInternal.getPath query building
- Attacker controls: query object values passed from public APIs
- Exploit idea: Leave a PAT from user A while user B's CAT is current, then start a wallet operation.
- Invariant to test: Both token types read by src/utils/toSearchParams.ts must belong to the same active subject.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: seed mismatched PAT and CAT subjects and assert toSearchParams (skips null/undefined refuses to return a token.
