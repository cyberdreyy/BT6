# Q1310: session-signer add falls back to delegation in embedded-wallets.ts

## Question
addSessionSigners delegates instead when the wallet is not TEE-backed; can an attacker use isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so a request the app described as adding a server signer instead grants a full delegation?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Call the add path with an on-device wallet.
- Invariant to test: A session-signer request must never silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) on an on-device wallet and assert the consent text matches the action.
