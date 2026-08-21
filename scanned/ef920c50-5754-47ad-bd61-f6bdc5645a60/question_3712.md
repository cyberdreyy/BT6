# Q3712: no expiry refresh for cached provider tokens in index.ts

## Question
getProviderAccessToken deletes the entry only when the decode throws or the token is expired; can an attacker exploit the gap between server-side revocation and local expiry so crossApp action barrel: loginWithCrossAppAuth keeps using a revoked token?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Revoke server-side and continue issuing actions locally.
- Invariant to test: Revocation must be detectable before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke and assert crossApp action barrel: loginWithCrossAppAuth fails on the next action.
