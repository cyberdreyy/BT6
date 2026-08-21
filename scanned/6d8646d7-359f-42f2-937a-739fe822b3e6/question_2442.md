# Q2442: getAccessTokenInternal prefers the privy access token in PrivyInternal.ts

## Question
getAccessTokenInternal returns the privy access token before the customer token; can an attacker cause PrivyInternal.fetch to hand a wallet operation a PAT that belongs to a previous user?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Leave a PAT from user A while user B's CAT is current, then start a wallet operation.
- Invariant to test: Both token types read by src/client/PrivyInternal.ts must belong to the same active subject.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: seed mismatched PAT and CAT subjects and assert PrivyInternal.fetch refuses to return a token.
