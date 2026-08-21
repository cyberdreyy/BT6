# Q2460: mfa cancelled treated as success in EmbeddedWalletApi.ts

## Question
errorIndicatesMfaCanceled checks error.code === 'mfa_canceled'; can an attacker make EmbeddedWalletApi.create treat a cancellation as a benign outcome so the calling app proceeds as if the operation was authorised?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Cancel an MFA prompt mid-operation and inspect what the operation returns.
- Invariant to test: A cancelled MFA must produce a failure the app cannot mistake for approval.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cancel during EmbeddedWalletApi.create and assert the returned promise rejects.
