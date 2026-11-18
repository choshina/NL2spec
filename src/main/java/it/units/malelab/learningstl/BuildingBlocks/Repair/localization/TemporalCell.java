/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair.localization;

public final class TemporalCell {
    public final int startIndex;
    public final int endIndex;
    public final double startTime;
    public final double endTime;
    public final boolean truth;
    public final int representativeIndex;
    public final double representativeTime;
    public final double representativeRobustness;

    public TemporalCell(
            int startIndex,
            int endIndex,
            double startTime,
            double endTime,
            boolean truth,
            int representativeIndex,
            double representativeTime,
            double representativeRobustness
    ) {
        this.startIndex = startIndex;
        this.endIndex = endIndex;
        this.startTime = startTime;
        this.endTime = endTime;
        this.truth = truth;
        this.representativeIndex = representativeIndex;
        this.representativeTime = representativeTime;
        this.representativeRobustness = representativeRobustness;
    }

    public boolean overlaps(double from, double to) {
        return endTime >= from && startTime <= to;
    }
}
