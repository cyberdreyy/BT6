# Q1782: debug logger prints session material in PrivyInternal.ts

## Question
The logger emits privy:refresh lines and error objects at DEBUG; can an attacker cause PrivyInternal.fetch to write token or code material into a log sink the app forwards off-device?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Enable DEBUG, run a refresh and a failed auth, and inspect the emitted lines.
- Invariant to test: No log line from src/client/PrivyInternal.ts may contain a token, verifier, or code value.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture logger output around PrivyInternal.fetch and assert no stored credential substring appears.
