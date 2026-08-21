# Q2112: fetchPrivyRoute is a public escape hatch in PrivyInternal.ts

## Question
privy.fetchPrivyRoute forwards arbitrary body, params, query and headers with the user's bearer token; can an attacker use PrivyInternal.fetch to invoke a sensitive route the SDK never exposes?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Call fetchPrivyRoute with a privileged route object and a crafted body.
- Invariant to test: Authenticated route access from src/client/PrivyInternal.ts must be limited to the SDK's own flows.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: call PrivyInternal.fetch with a wallet-mutating route and assert it is rejected or requires the same guards as the typed API.
