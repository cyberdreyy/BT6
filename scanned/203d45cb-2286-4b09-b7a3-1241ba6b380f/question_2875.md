# Q2875: unlink then relink races the session refresh in SmartWalletApi.ts

## Question
Can an attacker interleave an unlink and a link through SmartWalletApi.init so refreshSession observes the intermediate state and the app renders a linked-account set that no longer matches the server?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Fire unlink and link back to back and inspect the user object each returns.
- Invariant to test: The user object returned by each src/client/auth/SmartWalletApi.ts operation must reflect the state after that operation completed.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run unlink and link concurrently and assert the final returned linked_accounts equals a fresh user.get().
