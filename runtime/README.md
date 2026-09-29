# PostSteward embedded local runtime

This directory is the Post-Once-derived local runtime foundation adopted into the
PostSteward product.

Canonical provenance is recorded in
`POSTSTEWARD_RUNTIME_PROVENANCE.json`.

Important boundaries:

- this is now a **component of PostSteward**, not a separately marketed
  `post-once-bootstrap` product;
- the source snapshot came from the exact A-K standalone lineage;
- public identity, XDG paths, service names and cloud coordination are intentionally
  reworked in this repository after adoption;
- `AyobamiH/post-once` remains the owner's independent production/reference lineage;
- never point customer installations at the owner's original Post-Once state,
  credentials or services;
- provider OAuth secrets will remain in PostSteward Cloud; the local runtime will use
  an executor-fenced provider relay rather than creating split-brain provider authority.

See `../docs/ARCHITECTURE.md` and `../docs/IMPLEMENTATION_PLAN.md`.
