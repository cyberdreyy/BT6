# Q1068: relink loop reuses a stale token in signMessage.ts

## Question
getProviderAccessTokenOrRelink returns the cached token if present, otherwise relinks and reads again; can an attacker keep a stale token cached so crossApp signMessage: params [message skips the relink and operates with outdated authorization?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Cache a token whose scope was revoked and trigger the action.
- Invariant to test: Cached authorization must be revalidated before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cache a revoked token and assert crossApp signMessage: params [message revalidates.
