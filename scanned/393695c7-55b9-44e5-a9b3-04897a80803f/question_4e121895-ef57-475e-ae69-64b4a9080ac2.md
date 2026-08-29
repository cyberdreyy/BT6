` check? Test: build a two-hop promise chain (attacker -> relay -> victim) and assert victim always observes predecessor_id == relay's real account id, never an attacker-chosen override.
