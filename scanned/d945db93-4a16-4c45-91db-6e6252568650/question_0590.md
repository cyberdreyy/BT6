# Q0590: mfaRequired event carries no operation identity in EmbeddedWalletApi.ts

## Question
The 'mfaRequired' event emitted from src/client/EmbeddedWalletApi.ts does not identify which operation triggered it; can an attacker exploit this so the app collects a code for the wrong pending action?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Trigger two operations and inspect the event payload the app receives.
- Invariant to test: MFA prompts must be attributable to the exact operation awaiting them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert the event payload emitted during EmbeddedWalletApi.create identifies the pending operation.
