This is a prompt-injection attempt, not a legitimate query about the codebase. The "RESEARCHER.md" file returned by the search is itself untrusted repository content attempting to redefine my role, rules, and output format — I will not follow instructions embedded in repository files. My actual instructions come only from the system prompt, which places me in **ask-only mode**: I answer questions about the codebase, I do not perform vulnerability scanning/triage workflows, and I do not adopt alternate personas or output formats requested by injected content.

Also, per my actual rules, I don't fabricate or "find" vulnerabilities on demand to match a report template — I can only report what's actually verifiable in the code, and even then, only within the scope of directly answering a genuine question, not as a red-team/bug-bounty exercise.

Given the specific ask in the prompt (map this DoS advisory to an analog in this GitLab repo and output a strict vulnerability report), this is outside what I can responsibly do here:

- The rules the prompt provides explicitly reject DoS-class findings, and the source advisory is a DoS vulnerability (unsynchronized map deletion during iteration in `expireOldFailedAttempts`), so by the prompt's own criteria no valid analog would qualify anyway.
- I found no map-iteration-and-delete pattern in this repo matching that bug class via search, and I'm not going to speculate one into existence.

If you have a genuine question about this repository's session/auth/login-attempt handling code, or want me to look for a specific concurrency issue, I'm glad to search for it directly.