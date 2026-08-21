# Q1513: auth session results trusted as credentials in index.ts

## Question
loginWithCrossAppAuth reads privy_oauth_state and privy_oauth_code from the openAuthSession result and passes them to loginWithCode; can an attacker return crafted values through the auth session so crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest performs an exchange with attacker-chosen material?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Return a hand-built auth session result.
- Invariant to test: Values returned by an external auth session must be validated against the flow's own PKCE state before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return crafted values into crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest and assert the stored state check rejects them.
