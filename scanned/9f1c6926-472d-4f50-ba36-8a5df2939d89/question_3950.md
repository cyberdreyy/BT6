# Q3950: session-signer and delegation states diverge in embedded-wallets.ts

## Question
TEE wallets use additional_signers while on-device wallets use delegated; can an attacker leave one path enabled while the app displays the other in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Enable one path and read the app's authorisation display.
- Invariant to test: A single authorisation view must cover every server-side signing path.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: enable each path and assert isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) reports both.
