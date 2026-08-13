# Q3836: lending_pool_configure_bank: clone or copy helper can duplicate privileged state into the wrong object [a-partially-populated-config-opt] [rollback]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank` reach `lending_pool_configure_bank` with a partially populated config opt object with boundary values so protected state is cloned or copied into the wrong destination, violating `full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only` and causing `Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure_bank.rs` / `lending_pool_configure_bank`
- Entrypoint: `lending_pool_configure_bank`
- Attacker controls: a partially populated config opt object with boundary values
- Exploit idea: Attack any helper that duplicates config, fee state, emode, or metadata across objects and must bind source and destination tightly. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: full bank configuration must require exact authority and preserve every safety-critical field coherently on the intended bank only
- Expected Immunefi impact: Critical: privilege escalation or live bank misconfiguration enabling theft/bad debt
- Fast validation: Use mixed-validity source/destination objects and assert no protected state lands on an attacker-selected destination. Force the late failure branch and assert every protected field fully rolls back.
