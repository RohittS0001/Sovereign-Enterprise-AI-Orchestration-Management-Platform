# Release Process

## Definition of done

A story is done when the pull request is approved by at least one reviewer, all CI checks on
`main` pass, QA has signed off in Jira, and the change is behind a feature flag if it touches
payment capture.

## Release freeze

No production releases during the 5 days before a major merchant sale event. Hotfixes during
a freeze need written approval from the engineering manager.

## CI requirements

The `integration-tests` job must pass on `main` before any release. Two consecutive failing
`main` builds stop all merges until the build is green again.

## Sprint commitments

Teams commit to at most 85% of their average velocity of the last three sprints, minus planned
leave. Unplanned leave above 20% of team capacity triggers a scope review with the delivery lead.
