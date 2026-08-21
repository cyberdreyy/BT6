# Q0040: mfa satisfied by a non-mfa error in EmbeddedWalletApi.ts

## Question
withMfa only sets its mfa-required flag when the error type is 'missing_or_invalid_mfa'; can an unprivileged attacker make EmbeddedWalletApi.create's underlying operation fail and then succeed with a different error type so the retry completes without any MFA challenge?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Drive the operation through privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...}) and return an error whose type is not in the PrivyIframeErrorTypes list, then a success on retry.
- Invariant to test: No src/client/EmbeddedWalletApi.ts operation may complete after a failure without the MFA gate that failure demanded.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return an unrecognised error type on attempt 1 and success on attempt 2, and assert withMfa did not resolve without an MFA round.
