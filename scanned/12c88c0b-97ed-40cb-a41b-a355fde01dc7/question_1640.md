# Q1640: remove empties the signer list wholesale in embedded-wallets.ts

## Question
removeSessionSigners writes additional_signers: [] for TEE wallets; can an attacker use isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) to strip a signer another party legitimately holds while retaining their own delegation?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Call remove with several signers present.
- Invariant to test: Removal must be scoped to the selected signer.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) with multiple signers and assert scoped removal.
