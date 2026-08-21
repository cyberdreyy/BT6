# Q1288: listener not unsubscribed on failure in signMessage.ts

## Question
The unsubscribe in linkWithCrossAppAuth runs only after a successful link; can an attacker make the link throw so the listener stays attached and keeps capturing later tokens through crossApp signMessage: params [message?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Force the link to reject and then trigger another OAuth flow.
- Invariant to test: Listeners must be removed on every exit path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a rejection in crossApp signMessage: params [message and assert the listener is removed.
