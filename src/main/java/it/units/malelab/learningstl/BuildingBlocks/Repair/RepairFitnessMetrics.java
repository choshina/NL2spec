/*
 * Provenance: STL Repair addition; not present in the BUSTLE reference snapshot.
 * BUSTLE comparison reference: e945ad648036a05d788fe72f92b1ce6a41ddd98b.
 * This file builds on BUSTLE-derived interfaces but is a repository-specific extension.
 * See the package PROVENANCE.md and repository THIRD_PARTY.md.
 */
package it.units.malelab.learningstl.BuildingBlocks.Repair;

public class RepairFitnessMetrics {

    private final double deltaSError;
    private final double deltaSAll;
    private final double newError;
    private final double editCost;
    private final double repairFitness;
    private final int originalErrorCount;
    private final int originalCorrectCount;

    public RepairFitnessMetrics(
            double deltaSError,
            double deltaSAll,
            double newError,
            double editCost,
            double repairFitness,
            int originalErrorCount,
            int originalCorrectCount
    ) {
        this.deltaSError = deltaSError;
        this.deltaSAll = deltaSAll;
        this.newError = newError;
        this.editCost = editCost;
        this.repairFitness = repairFitness;
        this.originalErrorCount = originalErrorCount;
        this.originalCorrectCount = originalCorrectCount;
    }

    public double getDeltaSError() {
        return deltaSError;
    }

    public double getDeltaSAll() {
        return deltaSAll;
    }

    public double getNewError() {
        return newError;
    }

    public double getEditCost() {
        return editCost;
    }

    public double getRepairFitness() {
        return repairFitness;
    }

    public int getOriginalErrorCount() {
        return originalErrorCount;
    }

    public int getOriginalCorrectCount() {
        return originalCorrectCount;
    }
}
