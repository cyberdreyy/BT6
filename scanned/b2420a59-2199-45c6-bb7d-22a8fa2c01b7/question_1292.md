# Q1292: listener not unsubscribed on failure in index.ts

## Question
The unsubscribe in linkWithCrossAppAuth runs only after a successful link; can an attacker make the link throw so the listener stays attached and keeps capturing later tokens through crossApp action barrel: loginWithCrossAppAuth?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Force the link to reject and then trigger another OAuth flow.
- Invariant to test: Listeners must be removed on every exit path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a rejection in crossApp action barrel: loginWithCrossAppAuth and assert the listener is removed.
