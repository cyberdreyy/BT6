# Q1636: remove empties the signer list wholesale in delegateWallet.ts

## Question
removeSessionSigners writes additional_signers: [] for TEE wallets; can an attacker use delegateWallet: checks address belongs to user to strip a signer another party legitimately holds while retaining their own delegation?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Call remove with several signers present.
- Invariant to test: Removal must be scoped to the selected signer.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call delegateWallet: checks address belongs to user with multiple signers and assert scoped removal.
