# Q0700: clearMfa after refresh sees zero methods in EmbeddedWalletApi.ts

## Question
MfaApi calls proxy.clearMfa when the refreshed user reports mfa_methods.length === 0; can an attacker cause a stale or partial refresh so EmbeddedWalletApi.create clears MFA state while methods still exist?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Return a refresh response with an empty mfa_methods array during an unrelated operation.
- Invariant to test: MFA state may only be cleared when the server authoritatively reports no methods for that user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an empty mfa_methods for a user that has methods and assert EmbeddedWalletApi.create does not clear.
