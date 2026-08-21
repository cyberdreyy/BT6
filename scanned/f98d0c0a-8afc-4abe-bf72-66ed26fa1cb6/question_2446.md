# Q2446: getAccessTokenInternal prefers the privy access token in logger.ts

## Question
getAccessTokenInternal returns the privy access token before the customer token; can an attacker cause logger levels NONE/ERROR/WARN/INFO/DEBUG to hand a wallet operation a PAT that belongs to a previous user?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Leave a PAT from user A while user B's CAT is current, then start a wallet operation.
- Invariant to test: Both token types read by src/client/logger.ts must belong to the same active subject.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: seed mismatched PAT and CAT subjects and assert logger levels NONE/ERROR/WARN/INFO/DEBUG refuses to return a token.
