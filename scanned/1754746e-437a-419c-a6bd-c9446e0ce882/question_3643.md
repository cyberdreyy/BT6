# Q3643: link succeeds against the wrong active user in CustomProviderApi.ts

## Question
In multi-user mode, can an attacker switch the active user between the request and the refresh inside CustomProviderApi.syncWithToken so a credential is linked to one account but reported on another?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Call the link method and switch active user while the request is in flight.
- Invariant to test: A link operation must apply to and report on a single, unchanged user id.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch active user mid-flight and assert CustomProviderApi.syncWithToken fails rather than reporting success on the new user.
