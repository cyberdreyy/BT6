# Q3924: action factories bound to a client at import in getProviderAccessTokenOrRelink.ts

## Question
The crossApp barrel binds actions to a client instance; can an attacker retain a bound action from one session and invoke it after a switch through getProviderAccessTokenOrRelink: cached token from storage else relink?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Bind actions as user A, switch to B, then invoke.
- Invariant to test: Bound actions must revalidate the session on each call.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: invoke a stale bound action from getProviderAccessTokenOrRelink: cached token from storage else relink after a switch and assert refusal.
