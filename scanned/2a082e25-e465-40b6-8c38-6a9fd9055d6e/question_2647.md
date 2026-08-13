# Q2647: lending_pool_add_bank_permissionless: clone/add helper can duplicate sensitive config into a hostile destination [same-slot-permissionless-add-followed] [config-derivation]

## Question
Can an unprivileged attacker use `lending_pool_add_bank_permissionless` with same-slot permissionless add followed by a pricing or metadata helper path so `lending_pool_add_bank_permissionless` duplicates sensitive pool config into a hostile destination, violating `permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only` and causing `High: live bank misconfiguration, mispricing, or durable user freeze`? Focus specifically on derived bank config and authority surfaces created by pool-add helpers.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/add_pool_permissionless.rs` / `lending_pool_add_bank_permissionless`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: same-slot permissionless add followed by a pricing or metadata helper path
- Exploit idea: Audit clone/add helper paths that copy bank or emode parameters and must bind source/destination precisely. Focus specifically on derived bank config and authority surfaces created by pool-add helpers.
- Invariant to test: permissionless staked-collateral onboarding must derive and bind the exact intended validator, pool, mint, and bank context only
- Expected Immunefi impact: High: live bank misconfiguration, mispricing, or durable user freeze
- Fast validation: Attempt cross-bank clone/add with mismatched destinations and assert no protected config is copied into attacker-selected objects. Derive the resulting config under adversarial inputs and assert every safety-critical field and authority remains canonical.
