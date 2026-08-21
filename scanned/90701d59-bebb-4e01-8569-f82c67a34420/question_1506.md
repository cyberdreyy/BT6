# Q1506: auth session results trusted as credentials in isCrossAppWalletSmart.ts

## Question
loginWithCrossAppAuth reads privy_oauth_state and privy_oauth_code from the openAuthSession result and passes them to loginWithCode; can an attacker return crafted values through the auth session so isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets performs an exchange with attacker-chosen material?

## Target
- File/function: [src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts](src/action/crossApp/wallet/utils/isCrossAppWalletSmart.ts) - isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets
- Entrypoint: method selection between personal_sign and privy_signSmartWalletMessage
- Attacker controls: the address argument and duplicate addresses across accounts
- Exploit idea: Return a hand-built auth session result.
- Invariant to test: Values returned by an external auth session must be validated against the flow's own PKCE state before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return crafted values into isCrossAppWalletSmart: address membership in any cross_app account's smart_wallets and assert the stored state check rejects them.
