# Q0352: switchActiveUser accepts an unauthenticated id in PrivyInternal.ts

## Question
switchActiveUserId only checks membership in privy:saved-users; can an attacker make PrivyInternal.fetch switch to an id whose tokens are absent, so subsequent calls fall back to the null-keyed credentials of another account?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Add an id to saved-users, switch to it, then call getAccessToken.
- Invariant to test: Switching users must require that user's own stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: switch to a saved id with no tokens and assert getAccessToken returns null instead of the previous user's token.
