# Q0172: from address defaults to the wallet in smart-wallets.ts

## Question
handlePopulateTransaction and handleEstimateGas use `transaction.from ?? this._account.address` while the signature is produced by the wallet regardless; can an attacker set a from that differs from the signer so the populated nonce and gas describe a different account?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Send a transaction with a foreign from and compare the populated fields to the signing account.
- Invariant to test: Populated fields must be derived from the account that will actually sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign from to smart-wallets entry (BICONOMY and assert rejection or that population uses the signer address.
