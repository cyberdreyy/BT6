# Q0675: returned user object is not re-read from session in SmartWalletApi.ts

## Question
Does SmartWalletApi.init return the user object from the authenticate response (merged by mergeUser) without re-reading the freshly stored session, letting a stale or attacker-influenced response drive the app's is_new_user and linked_accounts view?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Return an authenticate response whose user payload disagrees with the token subject and observe which value reaches the setUser callback.
- Invariant to test: The user object handed to setUser must be consistent with the subject of the token that was just stored.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the response user.id differ from the JWT sub in the same response and assert SmartWalletApi.init rejects instead of calling setUser.
