# Q2080: user fetched twice per operation in embedded-wallets.ts

## Question
delegateWallet reads the user at the start and again at the end; can an attacker switch the active user between those reads so isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) reports a delegation on a different account?

## Target
- File/function: [src/utils/embedded-wallets.ts](src/utils/embedded-wallets.ts) - isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded)
- Entrypoint: every wallet-selection helper and delegation check
- Attacker controls: linked-account fields that decide embedded vs external classification
- Exploit idea: Switch the active user mid-call.
- Invariant to test: An operation must report on the identity it started with.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch identity mid-call in isEmbeddedWalletAccount (type wallet + wallet_client_type privy + connector_type embedded) and assert abort.
