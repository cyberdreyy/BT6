# Q0904: custom_api_url from app config redirects all traffic in UserApi.ts

## Question
PrivyInternal._initialize sets baseUrl from config.custom_api_url and flips isUsingServerCookies; can an unprivileged attacker influence that value so bearer tokens are sent to a different host?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Serve an app config with a custom_api_url and observe subsequent authenticated requests targeting it.
- Invariant to test: The API base URL must be pinned to a trusted set, not taken from a fetched config field.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return custom_api_url pointing elsewhere and assert UserApi.get does not send Authorization headers to that host.
