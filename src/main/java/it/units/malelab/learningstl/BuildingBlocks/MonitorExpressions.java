/*
 * Provenance: BUSTLE-derived file, modified in STL Repair.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * Original BUSTLE authorship is retained; local changes are identified in git history.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks;


public enum MonitorExpressions {

    PROP(".prop"),
    BOOL(".bool"),
    NOT(".not"),
    OR(".or"),
    AND(".and"),
    IMPLIES(".implies"),
    SINCE(".since"),
    HISTORICALLY(".historically"),
    ONCE(".once"),
    UNTIL(".until"),
    GLOBALLY(".globally"),
    EVENTUALLY(".eventually");

    private final String string;

    MonitorExpressions(String string) {
        this.string = string;
    }

    @Override
    public String toString() {
        return this.string;
    }

}
