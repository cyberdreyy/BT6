# Q1073: relink loop reuses a stale token in index.ts

## Question
getProviderAccessTokenOrRelink returns the cached token if present, otherwise relinks and reads again; can an attacker keep a stale token cached so crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest skips the relink and operates with outdated authorization?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Cache a token whose scope was revoked and trigger the action.
- Invariant to test: Cached authorization must be revalidated before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cache a revoked token and assert crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest revalidates.
