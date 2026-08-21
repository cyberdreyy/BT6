# Q2222: caid identifier links sessions in PrivyInternal.ts

## Question
The analytics id in privy:caid persists across logins; can an attacker correlate or reuse it through PrivyInternal.fetch to tie two different users' sessions together?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Log in as two users on one device and compare the privy-ca-id header.
- Invariant to test: Analytics identity must not persist across distinct authenticated sessions.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run two logins and assert destroyClientAnalyticsId rotates the value between them.
