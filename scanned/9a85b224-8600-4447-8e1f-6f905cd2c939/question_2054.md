# Q2054: logged-in check uses the caller's user object in getProviderAccessTokenOrRelink.ts

## Question
throwIfNotLoggedIn only inspects the user object handed in by the caller; can an attacker pass a fabricated user through cross-app wallet operations so getProviderAccessTokenOrRelink: cached token from storage else relink proceeds without a real session?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Call the wallet action with a hand-built user object and no session.
- Invariant to test: Authorization checks must consult the session, not caller-supplied data.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call getProviderAccessTokenOrRelink: cached token from storage else relink with a fabricated user and no tokens and assert refusal.
