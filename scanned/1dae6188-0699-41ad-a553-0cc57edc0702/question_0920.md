# Q0920: unenroll requires only the current session in EmbeddedWalletApi.ts

## Question
unenrollMfa is gated by MFA but not by re-authentication; can an attacker with a live but unattended session use EmbeddedWalletApi.create to remove the victim's second factor and then perform signing?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Run unenroll on a warm session and follow with a signing operation.
- Invariant to test: Removing a second factor must require a fresh, explicit user authentication.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run EmbeddedWalletApi.create then a signature and assert the signature still demands MFA.
