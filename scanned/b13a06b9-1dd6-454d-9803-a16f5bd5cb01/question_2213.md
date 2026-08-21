# Q2213: relying party string controlled by caller in CustomProviderApi.ts

## Question
In src/client/auth/CustomProviderApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Call CustomProviderApi.syncWithToken with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by CustomProviderApi.syncWithToken must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call CustomProviderApi.syncWithToken with a foreign relying party and assert the SDK refuses.
