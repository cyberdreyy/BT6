# Q0628: callback url supplied by the caller in signMessage.ts

## Question
The callbackUrl and redirectUrl come from the caller; can an attacker set them through privy.crossApp.wallet.signMessage({user, address, message, redirectUrl}) so the cross-app result (and any credential in the redirect) is delivered to an origin they control?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Call the action with an attacker-controlled redirectUrl.
- Invariant to test: Callback targets must be constrained to the app's configured origins.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a foreign redirectUrl to crossApp signMessage: params [message and assert rejection.
