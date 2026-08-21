# Q2262: versioned detection by a property name in smart-wallets.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so smart-wallets entry (BICONOMY takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to smart-wallets entry (BICONOMY and assert detection is structural.
