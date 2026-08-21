# Q3982: saved-users JSON parsed leniently in PrivyInternal.ts

## Question
getSavedUserIds JSON.parses the stored list and filters to strings, returning [] on failure; can an attacker seed a crafted list so PrivyInternal.fetch enumerates or switches to unintended ids?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Write a list containing crafted ids and call the switch path.
- Invariant to test: The saved-user list must be integrity-checked against stored credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign id and assert PrivyInternal.fetch rejects a switch to it.
