# Q3762: storeCustomerAccessToken accepts a non-string silently in PrivyInternal.ts

## Question
Passing a non-string to the store methods deletes the entry and emits 'token_cleared'; can an attacker use PrivyInternal.fetch to clear another session's credential by feeding a non-string through a reachable path?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Drive a response with a null token field and observe the deletion path.
- Invariant to test: Credential deletion must be an explicit operation, not the fallback for a malformed value.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass undefined through PrivyInternal.fetch and assert it raises rather than silently deleting.
