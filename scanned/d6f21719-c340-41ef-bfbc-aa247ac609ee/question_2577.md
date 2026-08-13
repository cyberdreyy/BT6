# Q2577: lending_pool_add_bank_permissionless: pool-add configuration conversion drops a safety parameter [validator-vote-and-pool-mint] [config-derivation]

## Question
Can an unprivileged attacker make `lending_pool_add_bank_permissionless` reach `lending_pool_add_bank_permissionless` with validator vote and pool/mint contexts from different staked assets so add-pool configuration conversion drops or corrupts a safety-critical parameter, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and leading to `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on derived bank config and authority surfaces created by pool-add helpers.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: validator vote and pool/mint contexts from different staked assets
- Exploit idea: Audit conversion from integration-specific config into bank config so no critical field silently defaults unsafely. Focus specifically on derived bank config and authority surfaces created by pool-add helpers.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Compare derived bank config against expected canonical config for adversarial inputs and assert all safety-critical fields survive exactly. Derive the resulting config under adversarial inputs and assert every safety-critical field and authority remains canonical.
