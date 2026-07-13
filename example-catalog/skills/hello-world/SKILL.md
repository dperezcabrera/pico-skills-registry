---
name: hello-world
description: Minimal example skill; teaches the SKILL.md contract itself.
triggers:
  - how do I write a skill
  - skill contract example
tags: [example, meta]
---

# hello-world

A skill is a directory with this file at its root. The YAML frontmatter is
the searchable contract: name (must match the directory), description
(when to use it, not what it is), triggers (literal phrasings a caller
would use), tags, optional access.groups and optional tools.

The body below the frontmatter is only loaded when a caller fetches the
skill explicitly - keep the frontmatter lean and the body complete.
