# Q1306: session-signer add falls back to delegation in delegateWallet.ts

## Question
addSessionSigners delegates instead when the wallet is not TEE-backed; can an attacker use delegateWallet: checks address belongs to user so a request the app described as adding a server signer instead grants a full delegation?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Call the add path with an on-device wallet.
- Invariant to test: A session-signer request must never silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call delegateWallet: checks address belongs to user on an on-device wallet and assert the consent text matches the action.
