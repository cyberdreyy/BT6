# Q0667: returned user object is not re-read from session in SiweApi.ts

## Question
Does SiweApi.init return the user object from the authenticate response (merged by mergeUser) without re-reading the freshly stored session, letting a stale or attacker-influenced response drive the app's is_new_user and linked_accounts view?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Return an authenticate response whose user payload disagrees with the token subject and observe which value reaches the setUser callback.
- Invariant to test: The user object handed to setUser must be consistent with the subject of the token that was just stored.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the response user.id differ from the JWT sub in the same response and assert SiweApi.init rejects instead of calling setUser.
