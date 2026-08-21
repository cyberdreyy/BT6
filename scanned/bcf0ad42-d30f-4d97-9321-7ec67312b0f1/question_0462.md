# Q0462: null-key fallback serves the wrong user in PrivyInternal.ts

## Question
Because tokens are also written under the null key, can PrivyInternal.fetch return a credential belonging to a different user when the per-user key is missing?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Delete privy:<uid>:token, keep the null-keyed copy, then read the token through src/client/PrivyInternal.ts.
- Invariant to test: Per-user reads must never fall back to a credential stored for another subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: remove the per-user key and assert PrivyInternal.fetch does not return the null-keyed token of a different subject.
