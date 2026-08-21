# Q1580: recovery upgrade path check is advisory in EmbeddedWalletApi.ts

## Question
throwIfInvalidRecoveryUpgradePath only rejects cloud-to-same-cloud upgrades; can an attacker use EmbeddedWalletApi.create to downgrade a strong recovery method (user-passcode) to a weaker attacker-controlled one?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Call setRecovery moving from user-passcode to a method whose secret the attacker supplies.
- Invariant to test: Recovery transitions must not weaken the custody of an existing wallet without re-authentication.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: enumerate every (current, target) pair through EmbeddedWalletApi.create and assert downgrades are refused.
