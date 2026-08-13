# Q2627: lending_pool_add_bank_permissionless: permissionless add-pool helper accepts the wrong staked collateral basis [a-bank-seed-reused-across] [config-derivation]

## Question
Can an unprivileged attacker call `lending_pool_add_bank_permissionless` with a bank seed reused across otherwise distinct candidate assets so `lending_pool_add_bank_permissionless` accepts the wrong staked collateral basis for a permissionless pool-add helper, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on derived bank config and authority surfaces created by pool-add helpers.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: a bank seed reused across otherwise distinct candidate assets
- Exploit idea: Probe derivation from vote accounts, single-pool mints, and onramp relationships so public onboarding cannot be pointed at the wrong economic object. Focus specifically on derived bank config and authority surfaces created by pool-add helpers.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Provide mixed vote/mint/onramp contexts and assert the permissionless add path rejects everything but the canonical relationship. Derive the resulting config under adversarial inputs and assert every safety-critical field and authority remains canonical.
