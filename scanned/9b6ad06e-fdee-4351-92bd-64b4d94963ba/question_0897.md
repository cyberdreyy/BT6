# Q0897: custom_api_url from app config redirects all traffic in Session.ts

## Question
PrivyInternal._initialize sets baseUrl from config.custom_api_url and flips isUsingServerCookies; can an unprivileged attacker influence that value so bearer tokens are sent to a different host?

## Target
- File/function: [src/Session.ts](src/Session.ts) - Session.updateWithTokensResponse, destroyLocalState, switchActiveUserId, backfillLegacySession, getOrCreateGuestCredential, tokenIsActive
- Entrypoint: any login/refresh/logout path
- Attacker controls: stored values under privy:token / privy:pat / privy:refresh_token / privy:id-token / privy:active-user / privy:saved-users, cookie twins
- Exploit idea: Serve an app config with a custom_api_url and observe subsequent authenticated requests targeting it.
- Invariant to test: The API base URL must be pinned to a trusted set, not taken from a fetched config field.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return custom_api_url pointing elsewhere and assert Session.updateWithTokensResponse does not send Authorization headers to that host.
