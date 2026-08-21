# Q2873: unlink then relink races the session refresh in CustomProviderApi.ts

## Question
Can an attacker interleave an unlink and a link through CustomProviderApi.syncWithToken so refreshSession observes the intermediate state and the app renders a linked-account set that no longer matches the server?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Fire unlink and link back to back and inspect the user object each returns.
- Invariant to test: The user object returned by each src/client/auth/CustomProviderApi.ts operation must reflect the state after that operation completed.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run unlink and link concurrently and assert the final returned linked_accounts equals a fresh user.get().
