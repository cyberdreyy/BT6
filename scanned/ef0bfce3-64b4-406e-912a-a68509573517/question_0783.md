# Q0783: unlink of the last identity leaves an orphan session in CustomProviderApi.ts

## Question
Can an attacker call CustomProviderApi.syncWithToken's unlink path to remove the only linked account that authenticated the session, then keep using the still-valid stored tokens on the now-unreachable account?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Unlink the sole identity, then call privy.getAccessToken() and a wallet operation with the retained credentials.
- Invariant to test: Removing the last authentication factor must invalidate the local session credentials.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: unlink the last account then assert Session.destroyLocalState ran and getAccessToken returns null.
