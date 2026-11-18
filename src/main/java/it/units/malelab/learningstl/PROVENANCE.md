# Java backend provenance

The original learning, tree, fitness, and local-search backbone in this
package is derived from BUSTLE:

<https://github.com/pigozzif/BUSTLESTLLearningFromData>

The exact upstream commit was not retained when this repository was imported.
For the provenance audit, this tree was compared against BUSTLE commit
`e945ad648036a05d788fe72f92b1ce6a41ddd98b` (2024-02-25). That comparison
found 51 Java files unchanged after excluding provenance headers, 16
BUSTLE-derived files with local modifications, and 18 files present only in
STL Repair. The complete per-file classification is in `PROVENANCE.tsv`.

This comparison commit is a reference snapshot, not a claim that it was the
exact originally imported revision. The identified upstream repository did
not contain an explicit software license when this notice was prepared.
Preserve upstream authorship and do not relicense these files without
permission.

## Source annotation convention

- `unchanged-from-bustle-reference`: kept byte-for-byte unchanged so that the
  upstream hash remains independently verifiable; these files intentionally
  do not receive a new STL Repair header.
- `bustle-derived-modified`: carries a source header identifying BUSTLE
  authorship and local STL Repair modifications.
- `stl-repair-addition`: carries a source header stating that the file was not
  present in the comparison snapshot and is a repository-specific extension
  built on BUSTLE-derived interfaces.

The classification describes textual provenance only. It does not grant a
license or decide legal derivation by itself.

Project-specific extensions include, among others:

- `EvaluateFormulaOnDataset.java`;
- `BuildingBlocks/Repair/`;
- seeded formula parsing and mapping used by repair experiments;
- modifications in `Main.java` that expose repair and localization modes.

Some extension files call BUSTLE-derived classes without necessarily being
textual derivatives themselves. Repository-level details and the BUSTLE paper
citation are in `../../../../../../../THIRD_PARTY.md`.

