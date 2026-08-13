# Q2663: lending_pool_add_bank_permissionless: pool-add path leaves a release-relevant bank misconfigured but live [same-slot-permissionless-add-followed] [config-derivation]

## Question
Can an unprivileged attacker make `lending_pool_add_bank_permissionless` reach `lending_pool_add_bank_permissionless` with same-slot permissionless add followed by a pricing or metadata helper path so the resulting bank is live but safety-misconfigured, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on derived bank config and authority surfaces created by pool-add helpers.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: same-slot permissionless add followed by a pricing or metadata helper path
- Exploit idea: Because release/pre-release integrations are in scope, look for add-pool paths that can materially misconfigure a future live bank if auth is bypassed or validation is incomplete. Focus specifically on derived bank config and authority surfaces created by pool-add helpers.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Derive the resulting config from adversarial inputs and assert live-enabling paths reject any configuration lacking all required conservative settings. Derive the resulting config under adversarial inputs and assert every safety-critical field and authority remains canonical.
