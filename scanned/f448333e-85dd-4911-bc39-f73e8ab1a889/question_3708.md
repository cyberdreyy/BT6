# Q3708: no expiry refresh for cached provider tokens in signMessage.ts

## Question
getProviderAccessToken deletes the entry only when the decode throws or the token is expired; can an attacker exploit the gap between server-side revocation and local expiry so crossApp signMessage: params [message keeps using a revoked token?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Revoke server-side and continue issuing actions locally.
- Invariant to test: Revocation must be detectable before privileged use.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: revoke and assert crossApp signMessage: params [message fails on the next action.
