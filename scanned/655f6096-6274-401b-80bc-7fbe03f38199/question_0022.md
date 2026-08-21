# Q0022: unverified JWT decode drives identity in PrivyInternal.ts

## Question
Token.parse uses jose.decodeJwt with no signature verification; can an unprivileged attacker reach PrivyInternal.fetch with a self-issued JWT-shaped string so its sub/exp fields are treated as identity or validity?

## Target
- File/function: [src/client/PrivyInternal.ts](src/client/PrivyInternal.ts) - PrivyInternal.fetch, _beforeRequest, _beforeRequestWithoutAuth, refreshSession, _refreshSession, getAccessToken, getAccessTokenInternal, getAppConfig, createAnalyticsEvent
- Entrypoint: every SDK API call
- Attacker controls: request bodies/params, retry behaviour (retries:3 on 408/409/425/5xx), app-config supplied custom_api_url, refresh dedupe cache key
- Exploit idea: Place a crafted unsigned JWT where src/client/PrivyInternal.ts reads a token and observe subject/expiry being consumed without verification.
- Invariant to test: No unverified JWT claim may determine identity, expiry or storage keying in src/client/PrivyInternal.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: hand PrivyInternal.fetch an unsigned JWT with an arbitrary sub and assert it is not accepted as an identity.
