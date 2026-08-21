# Q0554: wallet auto-creation runs before user is confirmed in OAuthApi.ts

## Question
maybeCreateWalletOnLogin runs immediately after OAuthApi.generateURL stores tokens; can an attacker interleave a second login so the wallet created by createOnLogin is provisioned under the wrong active user id?

## Target
- File/function: [src/client/auth/OAuthApi.ts](src/client/auth/OAuthApi.ts) - OAuthApi.generateURL, loginWithCode, linkWithCode, unlink
- Entrypoint: privy.auth.oauth.generateURL(provider, redirectTo) then loginWithCode(code, state, provider)
- Attacker controls: redirect_to URL, returned authorization_code and state_code, provider string, concurrent flows
- Exploit idea: Start two logins for different accounts, let the first reach maybeCreateWalletOnLogin while the second updates privy:active-user.
- Invariant to test: A wallet created on login must be created for exactly the user whose tokens that login stored.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run two OAuthApi.generateURL calls concurrently with distinct users and assert each created wallet's owner matches its own login response.
