# Q1530: empty signers array is meaningful in embedded-wallets.ts

## Question
addSessionSigners requires a non-empty array for TEE wallets but requires an empty one for on-device wallets; can an attacker exploit that inversion in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) so the wrong branch executes for the wallet type?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Call with an empty array for a TEE wallet and a populated one for an on-device wallet.
- Invariant to test: Branch selection and argument validation must be consistent per wallet type.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cross wallet type and signers shape in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert clear errors.
