# Q1072: relink loop reuses a stale token in index.ts

## Question
getProviderAccessTokenOrRelink returns the cached token if present, otherwise relinks and reads again; can an attacker keep a stale token cached so crossApp action barrel: loginWithCrossAppAuth skips the relink and operates with outdated authorization?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Cache a token whose scope was revoked and trigger the action.
- Invariant to test: Cached authorization must be revalidated before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cache a revoked token and assert crossApp action barrel: loginWithCrossAppAuth revalidates.
