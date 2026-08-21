# Q3704: no expiry refresh for cached provider tokens in getProviderAccessTokenOrRelink.ts

## Question
getProviderAccessToken deletes the entry only when the decode throws or the token is expired; can an attacker exploit the gap between server-side revocation and local expiry so getProviderAccessTokenOrRelink: cached token from storage else relink keeps using a revoked token?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Revoke server-side and continue issuing actions locally.
- Invariant to test: Revocation must be detectable before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke and assert getProviderAccessTokenOrRelink: cached token from storage else relink fails on the next action.
