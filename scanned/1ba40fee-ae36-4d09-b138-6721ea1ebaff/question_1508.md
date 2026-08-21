# Q1508: auth session results trusted as credentials in signMessage.ts

## Question
loginWithCrossAppAuth reads privy_oauth_state and privy_oauth_code from the openAuthSession result and passes them to loginWithCode; can an attacker return crafted values through the auth session so crossApp signMessage: params [message performs an exchange with attacker-chosen material?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Return a hand-built auth session result.
- Invariant to test: Values returned by an external auth session must be validated against the flow's own PKCE state before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return crafted values into crossApp signMessage: params [message and assert the stored state check rejects them.
