# Q2990: configure: cross-group or cross-bank object passes role checks [replay-of-a-previously-valid] [rollback]

## Question
Can an unprivileged attacker supply replay of a previously valid config layout under a new signer to `marginfi_group_configure` so `configure` accepts a signer/object combination from the wrong group or bank, violating `group-level delegate and admin updates must require the exact intended role and target the exact intended group only` and causing `Critical: privilege escalation to rewrite live protocol configuration`? Focus specifically on whether any protected field changes before a late binding or authorization failure.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/configure.rs` / `configure`
- Entrypoint: `marginfi_group_configure`
- Attacker controls: replay of a previously valid config layout under a new signer
- Exploit idea: Probe whether role checks bind authority only to the signer and not also to the exact target object being mutated. Focus specifically on whether any protected field changes before a late binding or authorization failure.
- Invariant to test: group-level delegate and admin updates must require the exact intended role and target the exact intended group only
- Expected Immunefi impact: Critical: privilege escalation to rewrite live protocol configuration
- Fast validation: Create multiple groups/banks and assert authorized keys for one context cannot mutate another context through shared struct shape. Force the late failure branch and assert every protected field fully rolls back.
