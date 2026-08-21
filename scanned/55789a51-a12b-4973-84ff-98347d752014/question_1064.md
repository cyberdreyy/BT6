# Q1064: relink loop reuses a stale token in getProviderAccessTokenOrRelink.ts

## Question
getProviderAccessTokenOrRelink returns the cached token if present, otherwise relinks and reads again; can an attacker keep a stale token cached so getProviderAccessTokenOrRelink: cached token from storage else relink skips the relink and operates with outdated authorization?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Cache a token whose scope was revoked and trigger the action.
- Invariant to test: Cached authorization must be revalidated before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cache a revoked token and assert getProviderAccessTokenOrRelink: cached token from storage else relink revalidates.
