# Q1622: openAuthSession is an injected dependency in index.ts

## Question
The action factories take openAuthSession from the caller; can an attacker supply an implementation through crossApp action barrel: loginWithCrossAppAuth that observes the authorization URL and the returned code?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Inject a logging implementation and inspect what it sees.
- Invariant to test: The auth-session transport must be trusted and not carry credentials it can retain.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert crossApp action barrel: loginWithCrossAppAuth does not pass reusable credentials through the injected transport.
