# Q3946: session-signer and delegation states diverge in delegateWallet.ts

## Question
TEE wallets use additional_signers while on-device wallets use delegated; can an attacker leave one path enabled while the app displays the other in delegateWallet: checks address belongs to user?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Enable one path and read the app's authorisation display.
- Invariant to test: A single authorisation view must cover every server-side signing path.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: enable each path and assert delegateWallet: checks address belongs to user reports both.
