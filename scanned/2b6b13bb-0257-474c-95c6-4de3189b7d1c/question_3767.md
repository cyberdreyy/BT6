# Q3767: lending_pool_configure_bank: delegate-role semantics differ across sibling config paths [same-slot-bank-config-attempt] [cross-object]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank` with same-slot bank-config attempt before user borrow/withdraw investigations so `lending_pool_configure_bank` reaches a sibling configuration effect through the wrong delegate role, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and causing `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: same-slot bank-config attempt before user borrow/withdraw investigations
- Exploit idea: Compare admin/curve/limit/flow/metadata/risk/emode role assumptions across related configuration entrypoints. Focus specifically on cross-group or cross-bank target confusion with a valid-looking signer/object pair.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Attempt each delegate signer against every related path and assert only the intended fields are mutable per role. Create multiple target objects and assert valid authority for one can never mutate another through shape-compatible metadata.
