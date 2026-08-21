# Q2790: password type check only in EmbeddedWalletApi.ts

## Question
create() rejects a non-string password but performs no strength or confirmation check; can an attacker set a trivial recovery password via EmbeddedWalletApi.create that later allows offline recovery?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Call create with a one-character password.
- Invariant to test: src/client/EmbeddedWalletApi.ts must enforce the app's recovery strength policy before provisioning.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call EmbeddedWalletApi.create with a weak password and assert the configured policy is enforced.
