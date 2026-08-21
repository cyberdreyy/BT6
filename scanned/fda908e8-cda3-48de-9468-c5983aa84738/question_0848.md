# Q0848: cached token validated only by decoded expiry in signMessage.ts

## Question
getProviderAccessToken parses the stored string with the unverified Token wrapper and only checks expiry; can an attacker place a self-issued JWT under that key so crossApp signMessage: params [message treats it as a valid provider token?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Write a crafted JWT with a distant exp under the storage key and trigger a cross-app action.
- Invariant to test: Cached credentials must be validated for provenance, not merely for expiry.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a crafted JWT and assert crossApp signMessage: params [message refuses to use it.
