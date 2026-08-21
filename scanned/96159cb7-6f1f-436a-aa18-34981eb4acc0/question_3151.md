# Q3151: provider app id not compared to the account in loginWithCrossAppAuth.ts

## Question
sendCrossAppRequest derives providerAppId from the resolved account, then matches it against the connections list; can an attacker construct state so the two disagree and loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` still proceeds?

## Target
- File/function: [src/action/crossApp/loginWithCrossAppAuth.ts](src/action/crossApp/loginWithCrossAppAuth.ts) - loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}`, redirectUrl) -> openAuthSession -> oauth.loginWithCode -> crossApp.updateOnCrossAppAuthentication
- Entrypoint: privy.crossApp.loginWithCrossAppAuth({providerAppId, redirectUrl})
- Attacker controls: providerAppId string, redirectUrl, the privy_oauth_state / privy_oauth_code values returned by the auth session
- Exploit idea: Return a connections entry whose provider_app_id matches a different account.
- Invariant to test: Provider identity must be consistent across account and connection.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: create disagreeing state and assert loginWithCrossAppAuth: oauth.generateURL(`privy:${providerAppId}` refuses.
