# Q2636: lending_pool_add_bank_permissionless: permissionless add-pool helper accepts the wrong staked collateral basis [cross-group-candidate-contexts-sharing] [market-identity]

## Question
Can an unprivileged attacker call `lending_pool_add_bank_permissionless` with cross-group candidate contexts sharing the same mint interface so `lending_pool_add_bank_permissionless` accepts the wrong staked collateral basis for a permissionless pool-add helper, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: cross-group candidate contexts sharing the same mint interface
- Exploit idea: Probe derivation from vote accounts, single-pool mints, and onramp relationships so public onboarding cannot be pointed at the wrong economic object. Focus specifically on exact economic identity of the external market, mint, reserve, or validator relationship.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Provide mixed vote/mint/onramp contexts and assert the permissionless add path rejects everything but the canonical relationship. Supply same-type accounts from a sibling market or pool and assert add-pool rejects every non-canonical identity.
