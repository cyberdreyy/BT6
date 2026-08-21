# Q3670: enrollment success not verified against refresh in EmbeddedWalletApi.ts

## Question
submitEnrollMfa returns the proxy result and then refreshes; can an attacker make EmbeddedWalletApi.create report a successful enrollment that the server never recorded?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Return a success from the iframe path while the refresh shows no methods.
- Invariant to test: Reported enrollment success must be confirmed by the refreshed user state.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return success with an empty mfa_methods refresh and assert EmbeddedWalletApi.create reports failure.
