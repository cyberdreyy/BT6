# Q0682: refresh dedupe keyed by literal 'key' in PrivyInternal.ts

## Question
refreshSession dedupes in-flight refreshes in a Map keyed by the refresh token, falling back to the literal 'key' when none exists; can an attacker make two different sessions share one in-flight refresh promise?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Trigger simultaneous refreshes with no refresh token present in multi-user mode and observe the shared cache entry.
- Invariant to test: Concurrent refreshes for different identities must never share a result.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two refreshes for different users with absent refresh tokens and assert two distinct requests are issued.
