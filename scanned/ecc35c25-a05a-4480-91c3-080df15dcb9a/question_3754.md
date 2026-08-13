# Q3754: lending_pool_configure_bank: cross-group or cross-bank object passes role checks [duplicate-metas-altering-target-bank] [rollback]

## Question
Can an unprivileged attacker supply duplicate metas altering target-bank interpretation to `lending_pool_configure_bank` so `lending_pool_configure_bank` accepts a signer/object combination from the wrong group or bank, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and causing `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: duplicate metas altering target-bank interpretation
- Exploit idea: Probe whether role checks bind authority only to the signer and not also to the exact target object being mutated. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Create multiple groups/banks and assert authorized keys for one context cannot mutate another context through shared struct shape. Force the late failure branch and assert every protected field fully rolls back.
