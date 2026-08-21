# Q2441: getAccessTokenInternal prefers the privy access token in InMemoryStorage.ts

## Question
getAccessTokenInternal returns the privy access token before the customer token; can an attacker cause InMemoryCache.get to hand a wallet operation a PAT that belongs to a previous user?

## Target
- File/function: [src/storage/InMemoryStorage.ts](src/storage/InMemoryStorage.ts) - InMemoryCache.get, put, del, getKeys (plain object _cache)
- Entrypoint: Privy({storage: new InMemoryCache()})
- Attacker controls: key strings reaching the object literal cache
- Exploit idea: Leave a PAT from user A while user B's CAT is current, then start a wallet operation.
- Invariant to test: Both token types read by src/storage/InMemoryStorage.ts must belong to the same active subject.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: seed mismatched PAT and CAT subjects and assert InMemoryCache.get refuses to return a token.
