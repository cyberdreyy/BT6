# Q2448: getAccessTokenInternal prefers the privy access token in toAbortSignalTimeout.ts

## Question
getAccessTokenInternal returns the privy access token before the customer token; can an attacker cause toAbortSignalTimeout (20s request abort signal) to hand a wallet operation a PAT that belongs to a previous user?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Leave a PAT from user A while user B's CAT is current, then start a wallet operation.
- Invariant to test: Both token types read by src/toAbortSignalTimeout.ts must belong to the same active subject.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: seed mismatched PAT and CAT subjects and assert toAbortSignalTimeout (20s request abort signal) refuses to return a token.
