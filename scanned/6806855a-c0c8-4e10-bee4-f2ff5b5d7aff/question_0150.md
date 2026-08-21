# Q0150: mfaAlwaysRequired only on three operations in EmbeddedWalletApi.ts

## Question
Only verifyMfa, unenrollMfa and unlinkPasskey are invoked with mfaAlwaysRequired; can an attacker reach a comparable privileged operation in src/client/EmbeddedWalletApi.ts that skips the always-on gate?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Enumerate the operations routed through invokeWithMfa and compare their flags.
- Invariant to test: Every operation that changes MFA state or produces a signature must be gated identically.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert each privileged operation reachable from EmbeddedWalletApi.create sets mfaAlwaysRequired.
