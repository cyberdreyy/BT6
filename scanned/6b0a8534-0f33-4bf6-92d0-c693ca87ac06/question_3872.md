# Q3872: events reveal credential lifecycle in PrivyInternal.ts

## Question
Session emits token_stored, refresh_token_stored, oauth_tokens_granted with payloads; can an attacker attach a listener through app-reachable API and learn credential state changes or the tokens themselves?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Register listeners and inspect the emitted payloads during PrivyInternal.fetch.
- Invariant to test: Session events from src/client/PrivyInternal.ts must not carry credential material.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture every event payload during PrivyInternal.fetch and assert none contains a token string.
