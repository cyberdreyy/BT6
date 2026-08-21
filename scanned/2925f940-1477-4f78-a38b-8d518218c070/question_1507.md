# Q1507: auth session results trusted as credentials in throwIfNotLoggedIn.ts

## Question
loginWithCrossAppAuth reads privy_oauth_state and privy_oauth_code from the openAuthSession result and passes them to loginWithCode; can an attacker return crafted values through the auth session so throwIfNotLoggedIn(user): only checks the user object passed by the caller performs an exchange with attacker-chosen material?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Return a hand-built auth session result.
- Invariant to test: Values returned by an external auth session must be validated against the flow's own PKCE state before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return crafted values into throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert the stored state check rejects them.
