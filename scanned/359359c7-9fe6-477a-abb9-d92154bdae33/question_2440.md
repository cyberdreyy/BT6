# Q2440: getAccessTokenInternal prefers the privy access token in LocalStorage.ts

## Question
getAccessTokenInternal returns the privy access token before the customer token; can an attacker cause LocalStorage.get (JSON.parse) to hand a wallet operation a PAT that belongs to a previous user?

## Target
- File/function: [src/storage/LocalStorage.ts](src/storage/LocalStorage.ts) - LocalStorage.get (JSON.parse), put (JSON.stringify), del, getKeys
- Entrypoint: every Session/pkce/crossApp storage operation
- Attacker controls: any value another SDK surface can write under a privy: key on the same origin
- Exploit idea: Leave a PAT from user A while user B's CAT is current, then start a wallet operation.
- Invariant to test: Both token types read by src/storage/LocalStorage.ts must belong to the same active subject.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: seed mismatched PAT and CAT subjects and assert LocalStorage.get (JSON.parse) refuses to return a token.
