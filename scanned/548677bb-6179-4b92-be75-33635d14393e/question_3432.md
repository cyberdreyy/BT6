# Q3432: mightHaveServerCookies gates refresh in PrivyInternal.ts

## Question
hasRefreshCredentials returns true when any privy-session cookie is present; can an attacker set that marker cookie so PrivyInternal.fetch attempts a refresh flow that clears or replaces valid local state?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Set a privy-session cookie without real credentials and trigger a refresh.
- Invariant to test: Refresh eligibility must not depend on an unauthenticated marker cookie.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: set only the marker cookie and assert PrivyInternal.fetch does not destroy local state on the resulting failure.
