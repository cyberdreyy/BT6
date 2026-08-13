# Q3774: lending_pool_configure_bank: delegate-role semantics differ across sibling config paths [replay-of-a-previously-valid] [rollback]

## Question
Can an unprivileged attacker use `lending_pool_configure_bank` with replay of a previously valid config layout under a new signer so `lending_pool_configure_bank` reaches a sibling configuration effect through the wrong delegate role, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and causing `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: replay of a previously valid config layout under a new signer
- Exploit idea: Compare admin/curve/limit/flow/metadata/risk/emode role assumptions across related configuration entrypoints. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Attempt each delegate signer against every related path and assert only the intended fields are mutable per role. Force the late failure branch and assert every protected field fully rolls back.
