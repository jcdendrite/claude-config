# References — branch-creation

Sources behind the naming rules in SKILL.md. Not loaded at skill runtime; read
manually when verifying a rule still holds or adding new guidance.

## Branch-naming conventions

### Conventional Branch
https://conventional-branch.github.io/

The `<type>/<ticket>-<topic>` form (`feature/GH-1234-checkout`) that the skill
declines to adopt as a global default. Grounds the "Why no `<type>/` prefix?"
section: the two conditions under which a type prefix earns its keep —
branch-prefix-keyed automation, and branches scanned without opening the
associated ticket — are the cases this convention is built for.

### Lullabot — Git branch naming ADR
https://architecture.lullabot.com/adr/20220920-git-branch-naming/

> "For our purposes we don't need the branch to indicate if it is a feature or
> a fix... Instead we rely on the ticket's type."

Reaches the same conclusion from the same tradeoff. The skill's argument rests
on its own two conditions and on the ticket system already carrying the work
type as a label — this ADR is recorded as independent corroboration, not as the
source the rule depends on.
