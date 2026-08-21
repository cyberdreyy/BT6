# Q0480: shared MfaPromises across operations in EmbeddedWalletApi.ts

## Question
MfaPromises.rootPromise/submitPromise are single mutable refs shared by every operation; can an attacker start two operations so the MFA answer supplied for the low-value one satisfies the high-value one?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Start a benign operation and a signing operation, then resolve the submit promise once.
- Invariant to test: An MFA response must satisfy only the operation that requested it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: start two EmbeddedWalletApi.create-routed operations and assert one submitted code cannot complete both.
