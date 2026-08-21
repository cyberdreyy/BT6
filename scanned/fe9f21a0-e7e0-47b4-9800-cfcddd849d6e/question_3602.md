# Q3602: cross-app login caches tokens before user confirmation in index.ts

## Question
loginWithCrossAppAuth calls updateOnCrossAppAuthentication with the oauth_tokens as soon as the exchange returns; can an attacker cause a token to be cached for a provider app the user never approved through crossApp action barrel: loginWithCrossAppAuth?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Return oauth_tokens for a different provider in the exchange response.
- Invariant to test: Cached provider tokens must match the provider the user authorised.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: return a foreign provider token to crossApp action barrel: loginWithCrossAppAuth and assert it is not cached.
