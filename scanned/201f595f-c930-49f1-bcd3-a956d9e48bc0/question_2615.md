# Q2615: lending_pool_add_bank_permissionless: pool-add fee or metadata side effects can be redirected [same-slot-permissionless-add-followed] [config-derivation]

## Question
Can an unprivileged attacker invoke `lending_pool_add_bank_permissionless` with same-slot permissionless add followed by a pricing or metadata helper path so `lending_pool_add_bank_permissionless` redirects flat-fee or metadata side effects during pool creation, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on derived bank config and authority surfaces created by pool-add helpers.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: same-slot permissionless add followed by a pricing or metadata helper path
- Exploit idea: Add-pool paths often transfer setup fees and initialize metadata; both should stay bound to canonical destinations/objects. Focus specifically on derived bank config and authority surfaces created by pool-add helpers.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Swap candidate destinations and metadata targets and assert add-pool cannot credit or initialize anything unvalidated. Derive the resulting config under adversarial inputs and assert every safety-critical field and authority remains canonical.
