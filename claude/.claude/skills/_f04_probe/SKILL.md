---
name: _f04_probe
description: >
  F-04 auto-trigger probe — test artifact, delete before PR opens.
  TRIGGER when: never (this skill exists only as a diff surface for the
  F-04 empirical test; it is not a real skill).
  DO NOT TRIGGER when: at all times.
user-invocable: false
---

F-04 probe skill body. If this text appears in a session transcript, the
skill was loaded by the harness. This is the expected marker string:
SKILL_BODY_F04_PROBE_LOADED.
